from autoqchem.db_functions import *
from autoqchem.molecule import *

app_path = "/home/ubuntu/github/auto-qchem/dash_app"


app_path = "C:/Users/AndrzejZuranski/Dropbox/DataX_PU/github/auto-qchem/dash_app"


def image(can):
    hash_str = hashlib.md5(can.encode()).hexdigest()

    if not os.path.exists(f"{app_path}/static/{hash_str}.svg"):
        mol = input_to_OBMol(can, input_type="string", input_format="can")
        OBMol_to_file(mol, "svg", f"{app_path}/static/{hash_str}.svg")
    return f"/static/{hash_str}.svg"


def get_table(tags, substructure):
    df = db_select_molecules(tags=tags, substructure=substructure)
    if df.empty:
        return df
    df['image'] = df.can.map(image).map(lambda path: f"![]({path})")
    df['detail'] = df['molecule_id'].map(lambda i: f'''|[detail](/detail/{str(i)})|\n|:----:|''')
    df['tags'] = df['tag'].map(lambda t: t.__repr__()[1:-1])

    df_metadata = pd.DataFrame(list(df.metadata))
    for c in ['class', 'subclass', 'type', 'subtype']:
        if c not in df_metadata.columns:
            df_metadata[c] = ''
    df_gaussian_config = pd.DataFrame(list(df_metadata.gaussian_config))
    df = pd.concat([df, df_gaussian_config, df_metadata[['class', 'subclass', 'type', 'subtype',
                                                         'max_num_conformers']]], axis=1)
    df['num_conf/max_conf'] = df.apply(lambda r: f"{r['num_conformers']}/{r['max_num_conformers']}", axis=1)
    df = df.drop(['num_conformers', 'max_num_conformers'], axis=1)
    df = df.loc[:, ~df.columns.duplicated()]  # deduplicate columns

    return df.drop(['molecule_id', 'metadata', '_ids'], axis=1)
