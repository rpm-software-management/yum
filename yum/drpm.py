#  Integrated delta rpm support
#  Copyright 2013 Zdenek Pavlas

#   This library is free software; you can redistribute it and/or
#   modify it under the terms of the GNU Lesser General Public
#   License as published by the Free Software Foundation; either
#   version 2.1 of the License, or (at your option) any later version.
#
#   This library is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#   Lesser General Public License for more details.
#
#   You should have received a copy of the GNU Lesser General Public
#   License along with this library; if not, write to the
#      Free Software Foundation, Inc.,
#      59 Temple Place, Suite 330,
#      Boston, MA  02111-1307  USA

from yum.constants import TS_UPDATE
from yum.Errors import RepoError
from yum.i18n import exception2msg, _
from yum.Errors import MiscError
from misc import checksum, repo_gen_decompress
from urlgrabber import grabber
async = hasattr(grabber, 'parallel_wait')
from xml.etree.cElementTree import iterparse
import os, gzip

APPLYDELTA = '/usr/bin/applydeltarpm'

class DeltaPackage:
    def __init__(self, rpm, size, remote, csum):
        # copy what needed
        self.rpm = rpm
        self.repo = rpm.repo
        self.basepath = rpm.basepath
        self.pkgtup = rpm.pkgtup

        # set up drpm attributes
        self.size = size
        self.relativepath = remote
        self.localpath = os.path.dirname(rpm.localpath) +'/'+ os.path.basename(remote)
        self.csum = csum

    def __str__(self):
        return 'Delta RPM of %s' % self.rpm

    def localPkg(self):
        return self.localpath

    def getDiscNum(self):
        return None

    def verifyLocalPkg(self):
        # check file size first
        try: fsize = os.path.getsize(self.localpath)
        except OSError: return False
        if fsize != self.size: return False

        # checksum
        ctype, csum = self.csum
        try: fsum = checksum(ctype, self.localpath)
        except MiscError: return False
        if fsum != csum: return False

        # hooray
        return True

class DeltaInfo:
    def __init__(self, ayum, pkgs):
        self.verbose_logger = ayum.verbose_logger
        self.jobs = {}
        self.limit = ayum.conf.deltarpm

        # calculate update sizes
        pinfo = {}
        reposize = {}
        for index, po in enumerate(pkgs):
            if not po.repo.deltarpm:
                continue
            if po.state != TS_UPDATE and po.name not in ayum.conf.installonlypkgs:
                continue
            pinfo.setdefault(po.repo, {})[po.pkgtup] = index
            reposize[po.repo] = reposize.get(po.repo, 0) + po.size

        # don't use deltas when deltarpm not installed
        if reposize and not os.access(APPLYDELTA, os.X_OK):
            self.verbose_logger.info(_('Delta RPMs disabled because %s not installed.'), APPLYDELTA)
            return

        # download delta metadata
        mdpath = {}
        for repo in reposize:
            self.limit = max(self.limit, repo.deltarpm)
            for name in ('prestodelta', 'deltainfo'):
                try: data = repo.repoXML.getData(name); break
                except: pass
            else:
                self.verbose_logger.warn(_('No Presto metadata available for %s'), repo)
                continue
            path = repo.cachedir +'/'+ os.path.basename(data.location[1])
            if not os.path.exists(path) and int(data.size) > reposize[repo]:
                self.verbose_logger.info(_('Not downloading Presto metadata for %s'), repo)
                continue

            def failfunc(e, name=name, repo=repo):
                mdpath.pop(repo, None)
                if hasattr(e, 'exception'): e = e.exception
                self.verbose_logger.warn(_('Failed to download %s for repository %s: %s'),
                                         name, repo, exception2msg(e))
            kwargs = {}
            if async and repo._async:
                kwargs['failfunc'] = failfunc
                kwargs['async'] = True
            try: mdpath[repo] = repo._retrieveMD(name, **kwargs)
            except Errors.RepoError, e: failfunc(e)
        if async:
            grabber.parallel_wait()

        # use installdict or rpmdb
        if ayum._up:
            installed = ayum._up.installdict.get
        else:
            installed = lambda (n, a): [
                (po.epoch, po.version, po.release)
                for po in ayum.rpmdb.searchNevra(n, None, None, None, a)]

        # parse metadata, populate self.deltas
        for repo, cpath in mdpath.items():
            pinfo_repo = pinfo[repo]
            path = repo_gen_decompress(cpath, 'prestodelta.xml',
                                       cached=repo.cache)
            for ev, el in iterparse(path):
                if el.tag != 'newpackage': continue
                new = el.get('name'), el.get('arch'), el.get('epoch'), el.get('version'), el.get('release')
                index = pinfo_repo.get(new)
                if index is not None:
                    po = pkgs[index]
                    best = po.size * 0.75 # make this configurable?
                    have = installed(new[:2]) or []
                    for el in el.findall('delta'):
                        size = int(el.find('size').text)
                        old = el.get('oldepoch'), el.get('oldversion'), el.get('oldrelease')
                        if size >= best or old not in have:
                            continue
                        best = size
                        remote = el.find('filename').text
                        csum = el.find('checksum')
                        csum = csum.get('type'), csum.text
                        pkgs[index] = DeltaPackage(po, size, remote, csum)
                el.clear()

    def wait(self, limit = 1):
        # wait for some jobs, run callbacks
        while len(self.jobs) >= limit:
            pid, code = os.wait()
            # urlgrabber spawns child jobs, too.  But they exit synchronously,
            # so we should never see an unknown pid here.
            assert pid in self.jobs
            callback = self.jobs.pop(pid)
            callback(code)

    def rebuild(self, po, adderror):
        # this runs when worker finishes
        def callback(code):
            if code != 0:
                return adderror(po, _('Delta RPM rebuild failed'))
            if not po.rpm.verifyLocalPkg():
                return adderror(po, _('Checksum of the delta-rebuilt RPM failed'))
            os.unlink(po.localpath)
            po.localpath = po.rpm.localpath # for --downloadonly

        # spawn a worker process
        self.wait(self.limit)
        pid = os.spawnl(os.P_NOWAIT, APPLYDELTA, APPLYDELTA, po.localpath, po.rpm.localpath)
        self.jobs[pid] = callback
