FROM continuumio/anaconda3:main

ENV LANG=C.UTF-8 LC_ALL=C.UTF-8
ENV PATH /opt/conda/bin:$PATH

# shell before container build
RUN apt-get update && \
    apt-get install -y wget bzip2 curl git libsm6 libxext6 libxrender-dev gnupg sudo tmux g++ && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# mongodb gpg key
RUN curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | \
    gpg -o /usr/share/keyrings/mongodb-server-7.0.gpg \
   --dearmor
RUN echo "deb [ signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] http://repo.mongodb.org/apt/debian bookworm/mongodb-org/7.0 main" | tee /etc/apt/sources.list.d/mongodb-org-7.0.list

RUN apt-get update \
    && apt-get install -y mongodb-org

RUN mkdir /home/git && cd /home/git && git clone https://github.com/eunjoolee122/yh-qchem.git
RUN cd /home/git && git clone https://github.com/doyle-lab-ucla/edboplus.git

RUN conda init bash \
    && . ~/.bashrc \ 
    && conda create --name autoqchem python=3.8 ipython \
    && conda activate autoqchem \
    && conda install -c conda-forge openbabel \
    && conda install -c conda-forge rdkit \ 
    && pip install --upgrade pip setuptools wheel \
    && pip install Cmake dash dash-bootstrap-components dash-bio jupyterlab \
    && cd /home/git/yh-qchem \ 
    && pip install -e . \
    && cd /home



