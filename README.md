# YUM

Yum is an automatic updater and installer for rpm-based systems.

Included programs:

    /usr/bin/yum		Main program

## Usage

Yum is run with one of the following options:

* `update [package list]`

  If run without any packages, Yum will automatically upgrade every currently
  installed package.  If one or more packages are specified, Yum will only
  update the packages listed.

* `install <package list>`

  Yum will install the latest version of the specified package (don't specify
  version information).

* `remove <package list>`

  Yum will remove the specified packages from the system.

* `list [package list]`

  List available packages.

See the man page for more information (`man yum`).  Also see:

* web page: http://yum.baseurl.org/

* wiki: http://yum.baseurl.org/wiki

```
3.2.X Branch - yum-3_2_X
      Starting commit is roughly: a3c91d7f6a15f31a42d020127b2da2877dfc137d
         E.g. git diff a3c91d7f6a15f31a42d020127b2da2877dfc137d
```

## Building

You can build an RPM package by running:

    $ make rpm

**Note:** Make sure you have `mock` and `lynx` installed.

## Development

You can spawn a Docker container for running your source checkout in isolation
from the host:

    $ make shell

This will build an image (if not built already), run it in a container and give
you a shell that has the `yum` command defined that runs code from the current
checkout:

    [root@b0172fbb3f3f /]# yum [...]

The checkout is bind-mounted into the container so any code changes you do on
the host will be reflected there.  Common debugging tools (`ipdb` and `pudb`)
are included in the image.

This should suffice for most development tasks.  If you're working on code that
requires a specific environment (such as the LVM snapshotting feature or
systemd integration), just create a VM instead, build an RPM with `make rpm`
and install it there.

### Configuration

By default, any operations you do with Yum in the container happen in a
separate installroot residing on a Docker data volume mounted at `/sandbox`.
This allows transactions to run at native I/O speeds and also ensures you start
with a pristine installroot that's not polluted with container-specific stuff.
To disable this, just remove the `installroot` line from `/etc/yum.conf` in the
container.

You can specify the container name:

    $ make CONTAINER_NAME=my-container shell

You can mount a host directory into the container at `/root` (e.g. to inject
your own dotfile configuration for the debugging tools):

    $ make HOME_DIR=/some/path shell

Finally, you can specify extra arguments to be passed to the `docker-run(1)`
command:

    $ make RUN_ARGS="--rm" shell
