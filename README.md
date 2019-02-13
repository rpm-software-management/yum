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

You can run Yum from the current checkout in a container as follows (make sure
you have the `podman` package installed):

    $ make shell

This will first build a CentOS 7 image (if not built already) and then run a
container with a shell where you can directly execute Yum:

    [root@bf03d3a43cbf /] yum

When you edit the code on your host, the changes you make will be immediately
reflected inside the container since the checkout is bind-mounted.

**Warning:** There's a (probably) bug in podman at the moment which makes it
not see symlinks in a freshly created container, which, in turn, makes Yum not
see the `/etc/yum.conf` symlink when it runs for the first time.  The
workaround is to `touch /etc/yum.conf` or simply re-run Yum.

**Note:** When you exit the container, it is not deleted but just stopped.  To
re-attach to it, use (replace the ID appropriately):

    $ podman start bf03d3a43cbf
    $ podman attach bf03d3a43cbf
