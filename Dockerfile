# Yum development image

FROM fedora:27

RUN rpm -e --nodeps dnf-yum \
    # Download all yum deps
    && dnf install -y --downloadonly --destdir=/tmp/yum yum \
    # Install the deps
    && rpm -iv /tmp/yum/*.rpm \
    # Replace real yum and dnf-yum with just rpmdb records (so that they don't
    # get pulled as a dependency later)
    && mv /etc/yum.conf{,.bak} \
    && rpm -e --nodeps yum dnf-yum \
    && rpm -iv --justdb /tmp/yum/{yum-3.4.3-,dnf-yum-}*.rpm \
    && mv /etc/yum.conf{.bak,} \
    && rm -rf /tmp/yum

# Install some useful tools
RUN dnf install -y \
        ipython \
        python-ipdb \
        python-pudb \
        python-rpmfluff \
        # This is not required by yum in fedora yet
        python-gpg \
        createrepo \
        less \
        vim \
        tmux \
        wget

# Prepare an optional installroot
RUN dnf --installroot=/sandbox --releasever=27 -y install system-release
VOLUME ["/sandbox"]

# Make invoking "yum" just work using the mounted source tree
ENV PATH=/src/bin:$PATH \
    PYTHONPATH=/src:$PYTHONPATH \
    LANG=en_US.UTF-8
VOLUME ["/src", "/root"]

ENTRYPOINT ["/bin/bash"]
