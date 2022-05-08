FROM ubuntu:20.04
MAINTAINER Eduard Pinconschi <eduard.pinconschi@tecnico.ulisboa.pt>
ENV PS1="\[\e[0;33m\]|> cgcrepair <| \[\e[1;35m\]\W\[\e[0m\] \[\e[0m\]# "
ENV TZ=Europe
ENV CORPUS_PATH="/usr/local/src/cgc"
ENV TOOLS_PATH="/usr/local/share/pyshared/cgc"
ARG threads=4

RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

################################
##### Install dependencies #####
################################
RUN apt update && apt -y upgrade && apt install -y -q git build-essential python2.7 python-dev python3-pip \
    python3-dev libc6-dev gcc-multilib g++-multilib gdb software-properties-common cmake curl clang

################################
## Install pip for Python 2.7 ##
################################
RUN curl https://bootstrap.pypa.io/pip/2.7/get-pip.py -o get-pip.py && python2 get-pip.py

RUN python2 -m pip install cppy==1.1.0 numpy==1.16.6 && \
    python2 -m pip install pycrypto==2.6.1 pyaml==20.4.0 matplotlib==2.1 defusedxml==0.7.1

WORKDIR /cgc
COPY . /cgc

################################
# Install tools and libraries ##
################################
RUN mkdir -p $TOOLS_PATH && cp -r tools/* $TOOLS_PATH && \
    mkdir -p "/cores" && mkdir -p $CORPUS_PATH && \
    mkdir -p "/usr/local/share/polls" && mkdir -p "/usr/local/lib/cgc/polls" && mkdir -p "/usr/local/share/povs" && \
    cp "./CMakeLists.txt" $CORPUS_PATH

RUN ./install_cgc_lib.sh

################################
######## Install orbis #########
################################
WORKDIR /opt
RUN git clone https://github.com/epicosy/orbis

WORKDIR /opt/orbis
ENV ORBIS_PLUGIN_PATH /cgc
RUN ./install.sh
RUN orbis init

#ENTRYPOINT ["orbis", "api", "-p", "8080"]
