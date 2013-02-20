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
from urlgrabber import grabber
async = hasattr(grabber, 'parallel_wait')
from xml.etree.cElementTree import iterparse
import os, gzip, subprocess

class Presto:
    def __init__(self, ayum, pkgs):
        self.verbose_logger = ayum.verbose_logger
        self.deltas = {}
        self._rpmsave = {}
        self.rpmsize = 0
        self.deltasize = 0

        # calculate update sizes
        pinfo = {}
        reposize = {}
        for po in pkgs:
            if not po.repo.presto:
                continue
            if po.state != TS_UPDATE and po.name not in ayum.conf.installonlypkgs:
                continue
            pinfo.setdefault(po.repo, {})[po.pkgtup] = po
            reposize[po.repo] = reposize.get(po.repo, 0) + po.size

        # download delta metadata
        mdpath = {}
        for repo in reposize:
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

        # parse metadata, populate self.deltas
        for repo, path in mdpath.items():
            pinfo_repo = pinfo[repo]
            if path.endswith('.gz'):
                path = gzip.open(path)
            for ev, el in iterparse(path):
                if el.tag != 'newpackage': continue
                new = el.get('name'), el.get('arch'), el.get('epoch'), el.get('version'), el.get('release')
                po = pinfo_repo.get(new)
                if po:
                    best = po.size * 0.75 # make this configurable?
                    have = ayum._up.installdict.get(new[:2], [])
                    for el in el.findall('delta'):
                        size = int(el.find('size').text)
                        old = el.get('oldepoch'), el.get('oldversion'), el.get('oldrelease')
                        if size >= best or old not in have:
                            continue
                        # the old version is installed, seq check should never fail. kill this?
                        seq = el.find('sequence').text
                        if subprocess.call(['/usr/bin/applydeltarpm', '-C', '-s', seq]) != 0:
                            self.verbose_logger.warn(_('Deltarpm sequence check failed for %s'), seq)
                            continue

                        best = size
                        csum = el.find('checksum')
                        csum = csum.get('type'), csum.text
                        self.deltas[po] = size, el.find('filename').text, csum
                el.clear()

    def to_drpm(self, po):
        try: size, remote, csum = self.deltas[po]
        except KeyError: return False
        self._rpmsave[po] = po.packagesize, po.relativepath, po.localpath

        # update stats
        self.rpmsize += po.packagesize
        self.deltasize += size

        # update size/path/checksum to drpm values
        po.packagesize = size
        po.relativepath = remote
        po.localpath = os.path.dirname(po.localpath) +'/'+ os.path.basename(remote)
        po.returnIdSum = lambda: csum
        return True

    def to_rpm(self, po):
        if po not in self._rpmsave:
            return
        # revert back to RPM
        po.packagesize, po.relativepath, po.localpath = self._rpmsave.pop(po)
        del po.returnIdSum

    def rebuild(self, po, adderror):
        # restore rpm values
        deltapath = po.localpath
        po.packagesize, po.relativepath, po.localpath = self._rpmsave.pop(po)
        del po.returnIdSum

        # rebuild it from drpm
        if subprocess.call(['/usr/bin/applydeltarpm', deltapath, po.localpath]) != 0:
            return adderror(po, _('Delta RPM rebuild failed'))
        # source drpm was already checksummed.. is this necessary?
        if not po.verifyLocalPkg():
            return adderror(po, _('Checksum of the delta-rebuilt RPM failed'))
        # no need to keep this
        os.unlink(deltapath)
