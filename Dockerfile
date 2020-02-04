# YUM development image

FROM centos:7

# Set up EPEL
RUN yum install -y \
        epel-release

# Install useful stuff
RUN yum install -y \
        python-pip \
        python-ipdb \
        ipython \
        vim \
        less
RUN rpm -e --nodeps yum
RUN rm -rf /var/cache/yum
RUN pip install --upgrade pip && pip install pudb

# Use the yum checkout mounted from the host
ENV PATH=/src/bin:$PATH \
    PYTHONPATH=/src:$PYTHONPATH
RUN ln -s /src/etc/yum.conf /etc/yum.conf
RUN ln -s /src/etc/version-groups.conf /etc/yum/version-groups.conf

VOLUME ["/src"]
ENTRYPOINT ["/bin/bash"]
