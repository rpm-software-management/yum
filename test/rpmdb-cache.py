#! /usr/bin/python -tt

import sys
import yum

__provides_of_requires_exact__ = False

yb1 = yum.YumBase()
yb1.conf.cache = True
yb2 = yum.YumBase()
yb2.conf.cache = True

if len(sys.argv) > 1 and sys.argv[1].lower() == 'full':
    print("Doing full test")
    __provides_of_requires_exact__ = True

assert hasattr(yb1.rpmdb, '__cache_rpmdb__')
yb1.rpmdb.__cache_rpmdb__ = False
yb2.setCacheDir()

# Version
ver1 = yb1.rpmdb.simpleVersion(main_only=True)[0]
ver2 = yb2.rpmdb.simpleVersion(main_only=True)[0]
if ver1 != ver2:
    print("Error: Version mismatch:", ver1, ver2, file=sys.stderr)

# Conflicts
cpkgs1 = yb1.rpmdb.returnConflictPackages()
cpkgs2 = yb2.rpmdb.returnConflictPackages()
if len(cpkgs1) != len(cpkgs2):
    print("Error: Conflict len mismatch:", len(cpkgs1),len(cpkgs2), file=sys.stderr)
for pkg in cpkgs1:
    if pkg not in cpkgs2:
        print("Error: Conflict cache missing", pkg, file=sys.stderr)
for pkg in cpkgs2:
    if pkg not in cpkgs1:
        print("Error: Conflict cache extra", pkg, file=sys.stderr)

# File Requires
frd1, blah, fpd1 = yb1.rpmdb.fileRequiresData()
frd2, blah, fpd2 = yb2.rpmdb.fileRequiresData()
if len(frd1) != len(frd2):
    print("Error: FileReq len mismatch:", len(frd1), len(frd2), file=sys.stderr)
for pkgtup in frd1:
    if pkgtup not in frd2:
        print("Error: FileReq cache missing", pkgtup, file=sys.stderr)
        continue
    if len(set(frd1[pkgtup])) != len(set(frd2[pkgtup])):
        print(("Error: FileReq[%s] len mismatch:" % (pkgtup,),
                             len(frd1[pkgtup]), len(frd2[pkgtup])), file=sys.stderr)
    for name in frd1[pkgtup]:
        if name not in frd2[pkgtup]:
            print(("Error: FileReq[%s] cache missing" % (pkgtup,),
                                 name), file=sys.stderr)
for pkgtup in frd2:
    if pkgtup not in frd1:
        print("Error: FileReq cache extra", pkgtup, file=sys.stderr)
        continue
    for name in frd2[pkgtup]:
        if name not in frd1[pkgtup]:
            print(("Error: FileReq[%s] cache extra" % (pkgtup,),
                                 name), file=sys.stderr)

# File Provides (of requires) -- not exact
if len(fpd1) != len(fpd2):
    print("Error: FileProv len mismatch:", len(fpd1), len(fpd2), file=sys.stderr)
for name in fpd1:
    if name not in fpd2:
        print("Error: FileProv cache missing", name, file=sys.stderr)
        continue

    if not __provides_of_requires_exact__:
        continue # We might be missing some providers

    if len(fpd1[name]) != len(fpd2[name]):
        print(("Error: FileProv[%s] len mismatch:" % (pkgtup,),
                             len(fpd1[name]), len(fpd2[name])), file=sys.stderr)
    for pkgtup in fpd1[name]:
        if pkgtup not in fpd2[name]:
            print("Error: FileProv[%s] cache missing" % name,pkgtup, file=sys.stderr)
for name in fpd2:
    if name not in fpd1:
        print("Error: FileProv cache extra", name, file=sys.stderr)
        continue
    for pkgtup in fpd2[name]:
        if pkgtup not in fpd1[name]:
            print("Error: FileProv[%s] cache extra" % name,pkgtup, file=sys.stderr)
