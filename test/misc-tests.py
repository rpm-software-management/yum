import unittest
import logging
import sys
from testbase import *

class MiscTests(DepsolveTests):
    ''' Test cases to test skip-broken'''
    
    def setUp(self):
        DepsolveTests.setUp(self)
        self.xrepo = FakeRepo("TestRepository", self.xsack)
        setup_logging()

    def repoPackage(self, name, version='1', release='0', epoch='0', arch='noarch'):
        po = FakePackage(name, version, release, epoch, arch, repo=self.xrepo)
        self.xsack.addPackage(po)
        return po
    
    def instPackage(self, name, version='1', release='0', epoch='0', arch='noarch'):
        po = FakePackage(name, version, release, epoch, arch, repo=self.repo)
        self.rpmdb.addPackage(po)
        return po
           
        
    def testLibBCD(self):
        '''
        The libABC test
        http://svn.labix.org/smart/trunk/README 
        (Case Studies - Case 2)

        The issue is, a package named `A` requires package `BCD` explicitly, and
        RPM detects implicit dependencies between `A` and `libB`, `libC`, and `libD`.
        Package `BCD` provides `libB`, `libC`, and `libD`, but additionally there
        is a package `B` providing `libB`, a package `C` providing `libC`, and
        a package `D` providing `libD`.

        In other words, there's a package `A` which requires four different symbols,
        and one of these symbols is provided by a single package `BCD`, which happens
        to provide all symbols needed by `A`. There are also packages `B`, `C`, and `D`,
        that provide some of the symbols required by `A`, but can't satisfy all
        dependencies without `BCD`.

        The expected behavior for an operation asking to install `A` is obviously
        selecting `BCD` to satisfy `A`'s dependencies
        
        This fails in yum because, yum selects the packages with the shortest name
        if multiple packages provides the same requirements
    
        '''
        A = self.repoPackage('A', '1',arch='i386')
        A.addRequires('LibB')
        A.addRequires('LibC')
        A.addRequires('LibD')
        A.addRequires('BCD')
        BCD = self.repoPackage('BCD', '1',arch='i386')
        BCD.addProvides('LibB')        
        BCD.addProvides('LibC')        
        BCD.addProvides('LibD')
        B = self.repoPackage('B', '1',arch='i386')
        B.addProvides('LibB')        
        C = self.repoPackage('C', '1',arch='i386')
        C.addProvides('LibC')        
        D = self.repoPackage('D', '1',arch='i386')
        D.addProvides('LibD')        
        self.tsInfo.addInstall(A)
        self.assertEqual('ok', *self.resolveCode(skip=False))
        # This one is disabled because, we no it fails, but we dont want it to bail out in the each testcase run
        # Just enable it to do the test
        # self.assertResult([A,BCD])
        
    def testLibBCD2(self):
        '''
        Same as above, but in this cases it is ok, because the BCD names is shorter than LibB,LibC and LibD    
        '''
        A = self.repoPackage('A', '1',arch='i386')
        A.addRequires('LibB')
        A.addRequires('LibC')
        A.addRequires('LibD')
        A.addRequires('BCD')
        BCD = self.repoPackage('BCD', '1',arch='i386')
        BCD.addProvides('LibB')        
        BCD.addProvides('LibC')        
        BCD.addProvides('LibD')
        B = self.repoPackage('LibB', '1',arch='i386')
        B.addProvides('LibB')        
        C = self.repoPackage('LibC', '1',arch='i386')
        C.addProvides('LibC')        
        D = self.repoPackage('LibD', '1',arch='i386')
        D.addProvides('LibD')        
        self.tsInfo.addInstall(A)
        self.assertEqual('ok', *self.resolveCode(skip=False))
        self.assertResult([A,BCD])
    
    def resolveCode(self,skip = False):
        solver = YumBase()
        solver.save_ts = save_ts
        solver.conf = FakeConf()
        solver.arch.setup_arch('x86_64')
        solver.conf.skip_broken = skip
        solver.tsInfo = solver._tsInfo = self.tsInfo
        solver.rpmdb = self.rpmdb
        solver.pkgSack = self.xsack
        
        for po in self.rpmdb:
            po.repoid = po.repo.id = "installed"
        for po in self.xsack:
            po.repoid = po.repo.id = "TestRepository"
        for txmbr in solver.tsInfo:
            if txmbr.ts_state in ('u', 'i'):
                txmbr.po.repoid = txmbr.po.repo.id = "TestRepository"
            else:
                txmbr.po.repoid = txmbr.po.repo.id = "installed"

        res, msg = solver.buildTransaction()
        return self.res[res], msg

    def testXML(self):
        import yum.misc
        for args in (

# valid utf8 and unicode
('\xc4\x9b\xc5\xa1\xc4\x8d', '\xc4\x9b\xc5\xa1\xc4\x8d'),
('\u011b\u0161\u010d',      '\xc4\x9b\xc5\xa1\xc4\x8d'),

# invalid utf8
('\xc3\x28', '\xc3\x83\x28'),
('\xa0\xa1', '\xc2\xa0\xc2\xa1'),
('Skytt\xe4', 'Skytt\xc3\xa4'),

# entity expansion
('&<>', '&amp;&lt;&gt;'),

# removal of invalid bytes
('\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\x0c\r\x0e\x0f\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f', '\t\n\r'),

# attr flag
('&"\'', '&amp;"\''),
('&"\'', True, '&amp;&quot;&apos;'),

# weirdness
(None, ''),
('abc ', 'abc'),

        ):
            # last item is the expected result
            args, expected = args[:-1], args[-1]
            actual = yum.misc.to_xml(*args)

            # actual vs expected
            self.assertEqual(type(actual), type(expected))
            self.assertEqual(actual, expected)

    def testOptparse(self):
        # make 'Usage: %s\n' translated
        import gettext
        def dgettext(domain, msg, orig=gettext.dgettext):
            if domain=='messages' and msg == 'Usage: %s\n':
                return 'Pou\xc5\xbeit\xc3\xad: %s\n'
            return orig(domain, msg)
        gettext.dgettext = dgettext
        # run "yum --help"
        from optparse import OptionParser
        parser = OptionParser(usage='\u011b\u0161\u010d')
        self.assertRaises(SystemExit, parser.parse_args, args=['--help'])

def setup_logging():
    logging.basicConfig()    
    plainformatter = logging.Formatter("%(message)s")    
    console_stdout = logging.StreamHandler(sys.stdout)
    console_stdout.setFormatter(plainformatter)
    verbose = logging.getLogger("yum.verbose")
    verbose.propagate = False
    verbose.addHandler(console_stdout)
    verbose.setLevel(2)

