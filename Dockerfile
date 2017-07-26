# Yum development image

FROM centos:7

# Install some useful tools
RUN yum install -y epel-release && yum install -y \
        createrepo \
        ipython \
        python-pip \
        vim \
        tmux
RUN pip install --upgrade pip setuptools && pip install \
        ipdb \
        pudb

# Prepare an optional installroot
RUN yum --installroot=/sandbox --releasever=7 -y install system-release
VOLUME ["/sandbox"]

# Remove the shipped installation of yum but keep it in the rpmdb so it's not
# accidentally reinstalled (e.g. as a dependency), also keep the config file
RUN yumdownloader --destdir=/tmp yum \
    && rpm -e --nodeps yum \
    && rpm -i --justdb /tmp/yum*.rpm \
    && rm /tmp/yum*.rpm \
    && rm -rf /var/cache/yum \
    && mv /etc/yum.conf{.rpmsave,}

# Make invoking "yum" just work using the mounted source tree
ENV PATH=/src/bin:$PATH \
    PYTHONPATH=/src:$PYTHONPATH \
    LANG=en_US.UTF-8
VOLUME ["/src", "/root"]

ENTRYPOINT ["/bin/bash"]
