import hashlib
import pickle
from contextlib import suppress

import appdirs

from autoqchem.db_local_functions import *
from autoqchem.gaussian_input_generator import *
from autoqchem.gaussian_log_extractor import *
from autoqchem.helper_functions import *
from autoqchem.helper_classes import *
from autoqchem.openbabel_utils import *

logger = logging.getLogger(__name__)


class pbs_manager(object):
    """PBS manager class."""

    def __init__(self, user, host, workdir=None, sftp_host=None, remote_dir=None, job_file=None):
        """Initialize pbs manager and load the cache file.

        :param user: username at remote host
        :type user: str
        :param host: remote host name
        :type host: str
        :param job_file: prefix for pbs_manager.pkl
        :type job_file: str
        """

        # set workdir and cache file
        if workdir is None:
            self.workdir = appdirs.user_data_dir(appauthor="yhqchem", appname=host.split('.')[0])   # appname = "nurion"
        else:
            self.workdir = workdir
        if job_file is not None:
            self.cache_file = os.path.join(self.workdir, f"{job_file}_pbs_manager.pkl")
        else:
            self.cache_file = os.path.join(self.workdir, "pbs_manager.pkl")
        os.makedirs(self.workdir, exist_ok=True)

        self.jobs = {}  # jobs under management

        # load jobs under management from cache_file (suppress exceptions, no file, empty file, etc.)
        with suppress(Exception):  # TODO this isn't very safe because may ignore important exceptions
            with open(self.cache_file, 'rb') as cf:
                self.jobs = pickle.load(cf)

        self.host = host
        self.user = user
        self.sftp_host = sftp_host
        if remote_dir:
            self.remote_dir = remote_dir
        else:
            self.remote_dir = f"/scratch/{self.user}/gaussian"
        self.connection = None
        self.sftp_connection = None

    def connect(self) -> None:
        """Connect to remote host."""

        create_new_connection = False
        # check if connection already exists
        if self.connection is not None:
            # check if it went stale
            if not self.connection.is_connected:
                logger.info(f"Connection got disconnected, reconnecting.")
                self.connection.close()
                self.connection = None
                create_new_connection = True
        else:
            logger.info(f"Creating connection to {self.host} as {self.user}")
            create_new_connection = True
        if create_new_connection:
            self.connection = ssh_connect_otp(self.host, self.user)
            self.connection.run(f"mkdir -p {self.remote_dir}")
            logger.info(f"Connected to {self.host} as {self.user}.")

    def connect_sftp(self) -> None:
        """Connect to remote sftp host."""

        create_new_connection = False
        # check if connection already exists
        if self.sftp_connection is not None:
            # check if it went stale
            if not self.sftp_connection.is_connected:
                logger.info(f"SFTP Connection got disconnected, reconnecting.")
                self.sftp_connection.close()
                self.sftp_connection = None
                create_new_connection = True
        else:
            logger.info(f"Creating connection to {self.sftp_host} as {self.user}")
            create_new_connection = True
        if create_new_connection:
            self.sftp_connection = ssh_connect_otp(self.sftp_host, self.user)
            logger.info(f"Connected to {self.sftp_host} as {self.user}.")
            
    def create_jobs_for_molecule(self,
                                 molecule,
                                 workflow_type="equilibrium",
                                 theory="APFD",
                                 solvent="None",
                                 light_basis_set="6-31G*",
                                 heavy_basis_set="LANL2DZ",
                                 generic_basis_set="genecp",
                                 max_light_atomic_number=36,
                                 wall_time='05:59:00') -> None:
        """Generate pbs jobs for a molecule. Gaussian input files are also generated.

        :param molecule: molecule object
        :type molecule: molecule
        :param workflow_type: Gaussian workflow type, allowed types are: 'equilibrium' or 'transition_state'
        :type workflow_type: str
        :param theory: Gaussian supported Functional (e.g., B3LYP)
        :type theory: str
        :param solvent: Gaussian supported Solvent (e.g., TETRAHYDROFURAN)
        :type solvent: str
        :param light_basis_set: Gaussian supported basis set for elements up to `max_light_atomic_number` (e.g., 6-31G*)
        :type light_basis_set: str
        :param heavy_basis_set: Gaussin supported basis set for elements heavier than `max_light_atomic_number` (e.g., LANL2DZ)
        :type heavy_basis_set: str
        :param generic_basis_set: Gaussian supported basis set for generic elements (e.g., gencep)
        :type generic_basis_set: str
        :param max_light_atomic_number: maximum atomic number for light elements
        :type max_light_atomic_number: int
        :param wall_time: wall time of the job in HH:MM:SS format
        :type wall_time: str
        """

        # create gaussian files
        molecule_workdir = os.path.join(self.workdir, molecule.inchikey)
        molecule_workdir = str(molecule_workdir).replace('\\', '/')
        gig = gaussian_input_generator(molecule, workflow_type, molecule_workdir, theory, solvent, light_basis_set,
                                       heavy_basis_set, generic_basis_set, max_light_atomic_number)
        gaussian_config = {'theory': theory,
                           'solvent': solvent,
                           'light_basis_set': light_basis_set,
                           'heavy_basis_set': heavy_basis_set,
                           'generic_basis_set': generic_basis_set,
                           'max_light_atomic_number': max_light_atomic_number}

        # DB check if the same molecule with the same gaussian configuration already exists
        exists, tags = db_check_exists(molecule.inchi, gaussian_config,
                                       molecule.max_num_conformers, molecule.conformer_engine)
        if exists:
            logger.warning(f"Molecule {molecule.inchi} already exists with the same Gaussian config with tags {tags}."
                           f" Not creating jobs.")
            return

        gig.create_gaussian_files()
        
        # create pbs files
        for gjf_file in glob.glob(f"{molecule_workdir}/*.gjf"):
            gjf_file = str(gjf_file).replace('\\', '/')
            base_name = os.path.basename(os.path.splitext(gjf_file)[0])
            self._create_pbs_file_from_gaussian_file(base_name, molecule_workdir, wall_time)
            # create job structure
            job = pbs_job(can=molecule.can,
                            inchi=molecule.inchi,
                            inchikey=molecule.inchikey,
                            elements=molecule.elements,
                            charges=molecule.charges,
                            connectivity_matrix=molecule.connectivity_matrix,
                            conformation=int(base_name.split("_conf_")[1]),
                            max_num_conformers=molecule.max_num_conformers,
                            tasks=gig.tasks,
                            config=gaussian_config,
                            conformer_engine=molecule.conformer_engine,
                            job_id=-1,  # job_id (not assigned yet)
                            directory=gig.directory,  # filesystem path
                            base_name=base_name,  # filesystem basename
                            status=pbs_status.created,
                            n_submissions=0,
                            n_success_tasks=0)  # status

            # create a key for the job
            key = hashlib.md5((job.can + str(job.conformation) +
                               str(job.max_num_conformers) + ','.join(map(str, job.tasks))).encode()).hexdigest()

            # check if a job like that already exists
            if key in self.jobs:  # a job like that is already present:
                logger.warning(f"A job with exactly the same parameters, molecule {job.can}, conformation "
                               f"{job.conformation}, workflow {job.tasks} already exists. "
                               f"Not creating a duplicate")
                continue

            self.jobs[key] = job  # add job to bag
        self._cache()
       
    def submit_jobs(self) -> None:
        """Submit jobs that have status 'created' to remote host."""

        jobs = self.get_jobs(pbs_status.created)
        logger.info(f"Submitting {len(jobs)} jobs.")
        self.submit_jobs_from_jobs_dict(jobs)

    def submit_jobs_from_jobs_dict(self, jobs) -> None:
        """Submit jobs to remote host.

        :param jobs: dictionary of jobs to submit
        :type jobs: dict
        """

        # check if there are any jobs to be submitted
        if jobs:
            # get or create connection
            self.connect()
            self.connect_sftp()

            # check if jobs are in status created or failed
            for name, job in jobs.items():
                # copy .sh and .gjf file to remote_dir
                self.sftp_connection.put(f"{job.directory}/{job.base_name}.sh", self.remote_dir)
                self.sftp_connection.put(f"{job.directory}/{job.base_name}.gjf", self.remote_dir)

                with self.connection.cd(self.remote_dir):
                    ret = self.connection.run(f"qsub {self.remote_dir}/{job.base_name}.sh", hide=True)
                    #job.job_id = re.search("job\s*(\d+)\n", ret.stdout).group(1)
                    match = re.search(r"(\d+)\.pbs", ret.stdout)
                    if match:
                        job.job_id = match.group(1)
                    job.status = pbs_status.submitted
                    job.n_submissions = job.n_submissions + 1
                    logger.info(f"Submitted job {name}, job_id: {job.job_id}.")

            self._cache()

    
    def parse_qstat_output(self):
        output = self.connection.run(f"qstat -u {self.user}", pty=False, timeout=5)
        # 각 줄을 개행 문자로 분리
        if output.stdout == '':
            return []
        else:
            lines = output.stdout.strip().split("\n")
    
            # 결과를 담을 리스트
            jobs = []
    
            # job 정보를 포함하는 줄들만 필터링
            for line in lines[4:]:
                # 첫 줄이 제목일 수 있으므로 길이가 충분한지 확인 후 처리
                if len(line) > 40 and not line.startswith("Job ID"):
                    # 공백이 여러 개로 구분되어 있으므로 정규 표현식으로 분리
                    columns = re.split(r'\s+', line)
    
                    # 결과를 딕셔너리로 저장
                    job_info = {
                        'Job ID': columns[0][:-4],
                        'Username': columns[1],
                        'Queue': columns[2],
                        'Jobname': columns[3],
                        'SessID': columns[4],
                        'Nodes': columns[5],
                        'Tasks': columns[6],
                        'Memory': columns[7],
                        'Req Time': columns[8],
                        'State': columns[9],
                        'Est Start Time': columns[10] if len(columns) > 10 else '--'
                    }
                    jobs.append(job_info)
    
            return jobs

    def retrieve_jobs(self) -> None:
        """Retrieve finished jobs from remote host and check which finished successfully and which failed."""

        ids_to_check = [j.job_id for j in self.get_jobs(pbs_status.submitted).values()]
        if not ids_to_check:
            logger.info(f"There are no jobs submitted to cluster. Nothing to retrieve.")
            return

        # get or create connection
        self.connect()

        # retrieve job ids that are running on the server
        jobs_remote = self.parse_qstat_output()
        user_running_ids = [job['Job ID'] for job in jobs_remote]
        running_ids = [id for id in user_running_ids if id in ids_to_check]
        finished_ids = [id for id in ids_to_check if id not in running_ids]

        logger.info(f"There are {len(running_ids)} running/pending jobs, {len(finished_ids)} finished jobs.")
        print(f"There are {len(running_ids)} running/pending jobs, {len(finished_ids)} finished jobs.")

        # get finished jobs
        finished_jobs = {name: job for name, job in self.jobs.items() if job.job_id in finished_ids}
        done_jobs = 0

        if finished_jobs:
            self.connect_sftp()
            logger.info(f"Retrieving log files of finished jobs.")
            for job in finished_jobs.values():
                status = self._retrieve_single_job(job)
                if status.value == pbs_status.done.value:
                    done_jobs += 1

            self._cache()
            logger.info(f"{done_jobs} jobs finished successfully (all Gaussian steps finished normally)."
                        f" {len(finished_jobs) - done_jobs} jobs failed.")

    def _retrieve_single_job(self, job) -> pbs_status:
        """Retrieve single job from remote host and check its status

        :param job: job
        :return: :py:meth:`~helper_classes.helper_classes.pbs_status`, resulting status
        """

        try:  # try to fetch the file
            log_file = self.sftp_connection.get(f"{self.remote_dir}/{job.base_name}.log",
                                           local=f"{job.directory}/{job.base_name}.log")

            # initialize the log extractor, it will try to read basic info from the file
            le = gaussian_log_extractor(log_file.local)
            
            try:  # look for more specific exception
                le.check_for_exceptions()

            except NoGeometryException:
                job.status = pbs_status.failed
                logger.warning(
                    f"Job {job.base_name} failed - the log file does not contain geometry. Cannot resubmit.")

            except NegativeFrequencyException:
                job.status = pbs_status.incomplete
                logger.warning(
                    f"Job {job.base_name} incomplete - log file contains negative frequencies. Resubmit job.")

            except OptimizationIncompleteException:
                job.status = pbs_status.incomplete
                logger.warning(f"Job {job.base_name} incomplete - geometry optimization did not complete.")

            except Exception as e:
                job.status = pbs_status.failed
                logger.warning(f"Job {job.base_name} failed with unhandled exception: {e}")

            if len(job.tasks) == le.n_tasks:
                job.status = pbs_status.done
            else:  # no exceptions were thrown, but still the job is incomplete
                job.status = pbs_status.incomplete
                logger.warning(f"Job {job.base_name} incomplete.")

        except FileNotFoundError:
            job.status = pbs_status.failed
            logger.warning(f"Job {job.base_name} failed  - could not retrieve log file. Cannot resubmit.")

        # clean up files on the remote site - do not cleanup anything, the /scratch/network cleans
        # up files that are older than 15 days

        return job.status

    def resubmit_incomplete_jobs(self, wall_time="05:59:00") -> None:
        """Resubmit jobs that are incomplete. If the job has failed because the optimization has not completed \
        and a log file has been retrieved, then \
        the last geometry will be used for the next submission. For failed jobs \
         the job input files will need to be fixed manually and submitted using the \
        function :py:meth:`~pbs_manager.pbs_manager.submit_jobs_from_jobs_dict`.\
         Maximum number of allowed submission of the same job is 3.

        :param wall_time: wall time of the job in HH:MM:SS format
        """

        incomplete_jobs = self.get_jobs(pbs_status.incomplete)
        incomplete_jobs_to_resubmit = {}

        if not incomplete_jobs:
            logger.info("There are no incomplete jobs to resubmit.")

        for key, job in incomplete_jobs.items():

            # put a limit on resubmissions
            if job.n_submissions >= 3:
                logger.warning(f"Job {job.base_name} has been already failed 3 times, not submitting again.")
                continue

            job_log = f"{job.directory}/{job.base_name}.log"
            job_gjf = f"{job.directory}/{job.base_name}.gjf"

            # replace geometry
            le = gaussian_log_extractor(job_log)
            le.get_atom_labels()
            le.get_geometry()
            # old coords block
            with open(job_gjf, "r") as f:
                file_string = f.read()
            old_coords_block = re.search(f"\w+\s+({float_or_int_regex})"
                                         f"\s+({float_or_int_regex})"
                                         f"\s+({float_or_int_regex}).*?\n\n",
                                         file_string, re.DOTALL).group(0)

            # new coords block
            coords = le.geom[list('XYZ')].applymap(lambda x: f"{x:.6f}")
            coords.insert(0, 'Atom', le.labels)
            coords_block = "\n".join(map(" ".join, coords.values)) + "\n\n"

            # make sure they are the same length and replace
            assert len(old_coords_block.splitlines()) == len(coords_block.splitlines())
            file_string = file_string.replace(old_coords_block, coords_block)
            with open(job_gjf, "w") as f:
                f.write(file_string)

            logger.info("Substituting last checked geometry in the new input file.")

            # replacing wall time
            job_sh = f"{job.directory}/{job.base_name}.sh"
            with open(job_sh, "r") as f:
                file_string = f.read()
            old_wall_time = re.search(f"#PBS -l walltime=(\d\d:\d\d:\d\d)", file_string).group(1)
            file_string = file_string.replace(old_wall_time, wall_time)
            with open(job_sh, "w") as f:
                f.write(file_string)
            convert_crlf_to_lf(job_sh)

            logger.info(f"Substituting wall_time with new value: {wall_time}")
            incomplete_jobs_to_resubmit[key] = job

        self.submit_jobs_from_jobs_dict(incomplete_jobs_to_resubmit)

    def upload_done_molecules_to_db(self, tags, RMSD_threshold=0.35) -> None:

        """Upload done molecules to db. Molecules are considered done when all jobs for a given \
         smiles are in 'done' status. The conformers are deduplicated and uploaded to database using a metadata tag.

        :param tags: a list of tags to create for this molecule
        :type tags: list(str)
        :param RMSD_threshold: RMSD threshold (in Angstroms) to use when deduplicating multiple conformers \
        after Gaussian has found optimal geometry
        :type RMSD_threshold: float
        """

        done_jobs = self.get_jobs(pbs_status.done)
        if not done_jobs:
            logger.info("There are no jobs in done status. Exitting.") 
            return

        # check if there are molecules with all jobs done
        dfj = self.get_job_stats(split_by_can=True)
        dfj_done = dfj[dfj['done'] == dfj.sum(1)]  # only done jobs
        done_cans = dfj_done.index.tolist()

        if not done_cans:
            logger.info("There are no molecules with all jobs done. Exitting.")
            return

        logger.info(f"There are {len(done_cans)} finished molecules {done_cans}.")
        logger.debug(f"Deduplicating conformers if RMSD < {RMSD_threshold}.")

        for done_can in done_cans:
            (keys, jobs) = zip(*self.get_jobs(can=done_can).items())
            print(keys)
            rdmol, energies, labels_ok = rdmol_from_slurm_jobs(jobs, postDFT=True)
            print(labels_ok)
            if labels_ok:
                keep = prune_rmsds(rdmol, RMSD_threshold)
                logger.info(f"Molecule {done_can} has {len(keys) - len(keep)} / {len(keys)} duplicate conformers.")

                # remove duplicate jobs
                can_keys_to_remove = [key for i, key in enumerate(keys) if i not in keep]
                to_remove_jobs = {name: job for name, job in self.jobs.items() if name in can_keys_to_remove}
                logger.info(
                    f"Removing {len(keys) - len(keep)} / {len(keys)} jobs and log files that contain duplicate conformers.")
                self.remove_jobs(to_remove_jobs)

                # upload non-duplicate jobs
                can_keys_to_keep = [key for i, key in enumerate(keys) if i in keep]
                self._upload_can_to_db(can_keys_to_keep, tags)
            else:
                for key in keys:
                    self.jobs[key].status = pbs_status.inspect
                self._cache()

    def _upload_can_to_db(self, keys, tags) -> None:
        """Uploading single molecule conformers to database.

        :param keys: list of keys to the self.jobs dictionary to upload
        :type keys: list
        :param tags: tags
        :type tags: str or list
        """

        # check if the tag(s) are properly provided
        assert isinstance(tags, (str, list))
        if isinstance(tags, str):
            tags = [tags]
        assert all(len(t.strip()) > 0 for t in tags)

        # fetch jobs
        jobs = [self.jobs[key] for key in keys]

        # get molecule info
        assert len(set(j.can for j in jobs)) == 1
        assert len(set(j.inchi for j in jobs)) == 1
        assert len(set(j.inchikey for j in jobs)) == 1
        assert len(set(tuple(j.elements) for j in jobs)) == 1
        assert len(set(tuple(j.charges) for j in jobs)) == 1
        assert len(set(tuple(j.connectivity_matrix.flatten()) for j in jobs)) == 1
        assert len(set(tuple(j.config) for j in jobs)) == 1

        mol_data = {'can': jobs[0].can,
                    'inchi': jobs[0].inchi,
                    'inchikey': jobs[0].inchikey,
                    'elements': jobs[0].elements,
                    'charges': jobs[0].charges.tolist(),
                    'connectivity_matrix': jobs[0].connectivity_matrix.flatten().tolist()}

        metadata = {'gaussian_config': jobs[0].config,
                    'gaussian_tasks': jobs[0].tasks,
                    'max_num_conformers': jobs[0].max_num_conformers,
                    'conformer_engine': jobs[0].conformer_engine}

        # loop over the conformers and extract info
        conformations = []
        logs = []
        for job in jobs:
            # extract descriptors for this conformer from log file
            log = f"{job.directory}/{job.base_name}.log"
            le = gaussian_log_extractor(log)
            # add descriptors to conformations list
            conformations.append(le.get_descriptors())
            logs.append(le.log)
        print(len(conformations))
        # compute weights
        free_energies = np.array(
            [Hartree_in_kcal_per_mol * c['descriptors']['G'] for c in conformations])  # in kcal_mol
        free_energies -= free_energies.min()  # to avoid huge exponentials
        weights = np.exp(-free_energies / (k_in_kcal_per_mol_K * T))
        weights /= weights.sum()

        mol_id = db_upload_molecule(mol_data, tags, metadata, weights, conformations, logs)
        logger.info(
            f"Uploaded descriptors to DB for smiles: {mol_data['can']}, number of conformers: {len(conformations)},"
            f" DB molecule id {mol_id}.")
        for key in keys:
            self.jobs[key].status = pbs_status.uploaded
        self._cache()

    def get_jobs(self, status=None, can=None, inchikey=None) -> dict:
        """Get a dictionary of jobs, optionally filter by status and canonical smiles.

        :param status: pbs status of the jobs
        :type status: pbs_status
        :param can: canonical smiles of the molecules, single string for one smiles, a list for multiple smiles
        :type can: str or list
        :return: dict
        """

        def match(job, status, can, inchikey):
            match = True
            if status is not None:
                match = match and job.status.value == status.value
            if can is not None:
                if isinstance(can, str):
                    can = [can]
                match = match and (job.can in can)
            if inchikey is not None:
                if isinstance(inchikey, str):
                    inchikey = [inchikey]
                match = match and (job.inchikey in inchikey)
            return match

        return {name: job for name, job in self.jobs.items() if match(job, status, can, inchikey)}

    def get_job_stats(self, split_by_can=False) -> pd.DataFrame:
        """Job stats for jobs currently under management, optionally split by canonical smiles.

        :param split_by_can: if True each canonical smiles will be listed separately
        :type split_by_can: bool
        :return: pandas.core.frame.DataFrame
        """

        df = pd.DataFrame([[v.status.name, v.can] for v in self.jobs.values()], columns=['status', 'can'])
        if split_by_can:
            return df.groupby(['status', 'can']).size().unstack(level=1).fillna(0).astype(int).T
        else:
            return df.groupby('status').size().to_frame('jobs').T

    def remove_jobs(self, jobs) -> None:
        """Remove jobs.

        :param jobs: dictionary of jobs to remove
        :type jobs: dict
        """

        self.connect()
        for name, job in jobs.items():
            logger.debug(f"Removing job {name}.")
            # remove local files
            try:
                os.remove(f"{job.directory}/{job.base_name}.sh")  # slurm file
                os.remove(f"{job.directory}/{job.base_name}.gjf")  # gaussian file
            except:
                pass
            if os.path.exists(f"{job.directory}/{job.base_name}.log"):
                os.remove(f"{job.directory}/{job.base_name}.log")  # log file
            # remove remote files
            self.connection.run(f"rm -f {self.remote_dir}/pbs-{job.job_id}.out")
            self.connection.run(f"rm -f {self.remote_dir}/{job.base_name}*")
            del self.jobs[name]
        self._cache()

    def squeue(self, summary=True) -> pd.DataFrame:
        """Run 'squeue -u $user' command on the server.

        :param summary: if True only a summary frame is displayed with counts of jobs in each status
        :return: pandas.core.frame.DataFrame
        """

        self.connect()
        if summary:
            ret = self.connection.run(f"squeue -u {self.user} -o %T", hide=True)
            status_series = pd.Series(ret.stdout.splitlines()[1:])
            return status_series.groupby(status_series).size().to_frame("jobs").T
        else:
            ret = self.connection.run(f"squeue -u {self.user}", hide=True)
            data = np.array(list(map(str.split, ret.stdout.splitlines())))
            return pd.DataFrame(data[1:], columns=data[0])

    def _scancel(self) -> None:
        """Run 'scancel -u $user' command on the server."""

        self.connect()
        self.connection.run(f"scancel -u {self.user}")
        self.remove_jobs(self.get_jobs(status=pbs_status.submitted))

    def _cache(self) -> None:
        """save jobs under management and cleanup empty directories"""

        with open(self.cache_file, 'wb') as cf:
            pickle.dump(self.jobs, cf)

        cleanup_empty_dirs(self.workdir)

    def _create_pbs_file_from_gaussian_file(self, base_name, directory, wall_time) -> None:
        """Generate a single pbs submission file based on the Gaussian input file.

        :param base_name: base name of the Gaussian file
        :param directory: directory location of the Gaussian file
        :param wall_time: wall time of the job in HH:MM:SS format
        """

        # get information from gaussian file needed for submission
        with open(f"{directory}/{base_name}.gjf") as f:
            file_string = f.read()

        host = self.host.split(".")[0]

        n_processors = re.search("nprocshared=(.*?)\n", file_string).group(1)

        output = ""
        output += f"#!/bin/bash\n"
        output += f"#PBS -V\n"
        output += f"#PBS -N {base_name}\n"
        output += f"#PBS -q normal\n"
        if host == "nurion":
            output += f"#PBS -l select=1:ncpus={n_processors}:mpiprocs=1:ompthreads={n_processors}\n" \
                      f"#PBS -l walltime={wall_time}\n" \
                      f"#PBS -A gaussian\n\n"
        else:
            output += f"#PBS -l select=1:" \
                      f"#PBS -l ncpus={n_processors}:mpiprocs=1:ompthreads={n_processors}\n" \
                      f"#PBS -l walltime={wall_time}\n" \
                      f"#PBS -A gaussian\n\n"

        output += f"cd $PBS_O_WORKDIR\n\n"
        output += f"module purge\n"
        output += f"module load gaussian/g16.a03\n\n"
        output += f"export GAUSS_SCRDIR='/scratch/{self.user}/gaussian'\n"
        output += f"export GAUSS_PDEF=$NCPUS\n\n"
        output += f"input={base_name}\n\n"
        output += f"# run the code \n" \
                  f"g16 ${{input}}.gjf\n\n"

        sh_file_path = f"{directory}/{base_name}.sh"
        with open(sh_file_path, "w") as f:
            f.write(output)
        convert_crlf_to_lf(sh_file_path)
        logger.debug(f"Created a PBS job file in {sh_file_path}")
