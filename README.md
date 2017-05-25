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
