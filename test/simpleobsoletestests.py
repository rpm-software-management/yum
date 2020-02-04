from testbase import *

class SimpleObsoletesTests(OperationsTests):

    @staticmethod
    def buildPkgs(pkgs, *args):
        # installed
        pkgs.installed_i386 = FakePackage('zsh', '1', '1', '0', 'i386')
        pkgs.installed_x86_64 = FakePackage('zsh', '1', '1', '0', 'x86_64')
        pkgs.installed_noarch = FakePackage('zsh', '1', '1', '0', 'noarch')
        # obsoletes
        pkgs.obsoletes_i386 = FakePackage('zsh-ng', '0.3', '1', '0', 'i386')
        pkgs.obsoletes_i386.addObsoletes('zsh', None, (None, None, None))
        pkgs.obsoletes_i386.addProvides('zzz')
        pkgs.obsoletes_x86_64 = FakePackage('zsh-ng', '0.3', '1', '0', 'x86_64')
        pkgs.obsoletes_x86_64.addObsoletes('zsh', None, (None, None, None))
        pkgs.obsoletes_x86_64.addProvides('zzz')
        pkgs.obsoletes_noarch = FakePackage('zsh-ng', '0.3', '1', '0', 'noarch')
        pkgs.obsoletes_noarch.addObsoletes('zsh', None, (None, None, None))
        pkgs.obsoletes_noarch.addProvides('zzz')
        # requires obsoletes
        pkgs.requires_obsoletes = FakePackage('superzippy', '3.5', '3', '0', 'noarch')
        pkgs.requires_obsoletes.addRequires('zzz')

    # noarch to X

    def testObsoletenoarchTonoarch(self):
        p = self.pkgs
        res, msg = self.runOperation(['update'], [p.installed_noarch], [p.obsoletes_noarch])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_noarch,))
    def testObsoletenoarchTonoarchForDependency(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'superzippy'], [p.installed_noarch],
                                     [p.obsoletes_noarch, p.requires_obsoletes])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_noarch, p.requires_obsoletes))

    def testObsoletenoarchToi386(self):
        p = self.pkgs
        res, msg = self.runOperation(['update'], [p.installed_noarch], [p.obsoletes_i386],
                                     {'multilib_policy': 'all'})
        self.assert_(res=='ok', msg)
        self.assertResult((p.obsoletes_i386,))
    def testObsoletenoarchToi386ForDependency(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'superzippy'], [p.installed_noarch],
                                     [p.obsoletes_i386, p.requires_obsoletes])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_i386, p.requires_obsoletes))

    def testObsoletenoarchTox86_64(self):
        p = self.pkgs
        res, msg = self.runOperation(['update'], [p.installed_noarch], [p.obsoletes_x86_64],
                                     {'multilib_policy': 'all'})
        self.assert_(res=='ok', msg)
        self.assertResult((p.obsoletes_x86_64,))
    def testObsoletenoarchTox86_64ForDependency(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'superzippy'], [p.installed_noarch],
                                     [p.obsoletes_x86_64, p.requires_obsoletes])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_x86_64, p.requires_obsoletes))

    def testObsoletenoarchToMultiarch(self):
        p = self.pkgs
        res, msg = self.runOperation(['update'], [p.installed_noarch], [p.obsoletes_i386, p.obsoletes_x86_64],
                                     {'multilib_policy': 'all'})
        self.assert_(res=='ok', msg)
        if new_behavior:
            self.assertResult((p.obsoletes_x86_64,), (p.obsoletes_i386,))
        else:
            self.assertResult((p.obsoletes_i386, p.obsoletes_x86_64))
    def testObsoletenoarchToMultiarchForDependency(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'superzippy'], [p.installed_noarch],
                                     [p.obsoletes_i386, p.obsoletes_x86_64, p.requires_obsoletes])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_x86_64, p.requires_obsoletes), (p.obsoletes_i386,))

    # i386 to X

    def testObsoletei386Tonoarch(self):
        p = self.pkgs
        res, msg = self.runOperation(['update'], [p.installed_i386], [p.obsoletes_noarch])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_noarch,))
    def testObsoletei386TonoarchForDependency(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'superzippy'], [p.installed_i386], [p.obsoletes_noarch, p.requires_obsoletes])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_noarch, p.requires_obsoletes))

    def testObsoletei386Toi386(self):
        p = self.pkgs
        res, msg = self.runOperation(['update'], [p.installed_i386], [p.obsoletes_i386])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_i386,))
    def testObsoletei386Toi386ForDependency(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'superzippy'], [p.installed_i386], [p.obsoletes_i386, p.requires_obsoletes])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_i386, p.requires_obsoletes))

    def testObsoletei386Tox86_64(self):
        p = self.pkgs
        res, msg = self.runOperation(['update'], [p.installed_i386], [p.obsoletes_x86_64])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_x86_64,))
    def testObsoletei386Tox86_64ForDependency(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'superzippy'], [p.installed_i386], [p.obsoletes_x86_64, p.requires_obsoletes])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_x86_64, p.requires_obsoletes))


    def testObsoletei386ToMultiarch(self):
        p = self.pkgs
        res, msg = self.runOperation(['update'], [p.installed_i386], [p.obsoletes_i386, p.obsoletes_x86_64])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_i386,))
    def testObsoletei386ToMultiarchForDependency(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'superzippy'], [p.installed_i386], [p.obsoletes_i386, p.obsoletes_x86_64, p.requires_obsoletes])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_i386, p.requires_obsoletes))

    # x86_64 to X

    def testObsoletex86_64Tonoarch(self):
        p = self.pkgs
        res, msg = self.runOperation(['update'], [p.installed_x86_64], [p.obsoletes_noarch])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_noarch,))
    def testObsoletex86_64TonoarchForDependency(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'superzippy'], [p.installed_x86_64], [p.obsoletes_noarch, p.requires_obsoletes])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_noarch, p.requires_obsoletes))

    def testObsoletex86_64Toi386(self):
        p = self.pkgs
        res, msg = self.runOperation(['update'], [p.installed_x86_64], [p.obsoletes_i386])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_i386,))
    def testObsoletex86_64Toi386ForDependency(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'superzippy'], [p.installed_x86_64], [p.obsoletes_i386, p.requires_obsoletes])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_i386, p.requires_obsoletes))

    def testObsoletex86_64Tox86_64(self):
        p = self.pkgs
        res, msg = self.runOperation(['update'], [p.installed_x86_64], [p.obsoletes_x86_64])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_x86_64,))
    def testObsoletex86_64Tox86_64ForDependency(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'superzippy'], [p.installed_x86_64], [p.obsoletes_x86_64, p.requires_obsoletes])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_x86_64, p.requires_obsoletes))

    def testObsoletex86_64ToMultiarch1(self):
        p = self.pkgs
        res, msg = self.runOperation(['update'], [p.installed_x86_64], [p.obsoletes_i386, p.obsoletes_x86_64])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_x86_64,))
    def testObsoletex86_64ToMultiarch2(self):
        p = self.pkgs
        res, msg = self.runOperation(['update'], [p.installed_x86_64], [p.obsoletes_x86_64, p.obsoletes_i386])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_x86_64,))
    def testInstallObsoletex86_64ToMultiarch1(self):
        # Found by BZ 593349, libgfortran43/44
        p = self.pkgs
        res, msg = self.runOperation(['install', 'zsh.x86_64'], [], [p.installed_x86_64, p.installed_i386, p.obsoletes_x86_64, p.obsoletes_i386])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_x86_64,))
    def testInstallObsoletex86_64ToMultiarch2(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'zsh.i386'], [], [p.installed_x86_64, p.installed_i386, p.obsoletes_x86_64, p.obsoletes_i386])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_i386,))
    def testInstallObsoletex86_64ToMultiarch3(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'zsh'], [], [p.installed_noarch, p.obsoletes_x86_64, p.obsoletes_i386])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_x86_64,))
    def testObsoletex86_64ToMultiarchForDependency(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'superzippy'],
                                     [p.installed_x86_64], [p.obsoletes_i386, p.obsoletes_x86_64, p.requires_obsoletes])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_x86_64, p.requires_obsoletes))

    # multiarch to X

    def testObsoleteMultiarchTonoarch(self):
        p = self.pkgs
        res, msg = self.runOperation(['update'], [p.installed_i386, p.installed_x86_64], [p.obsoletes_noarch])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_noarch,))
    def testObsoleteMultiarchTonoarchForDependency(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'superzippy'], [p.installed_i386, p.installed_x86_64], [p.obsoletes_noarch, p.requires_obsoletes])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_noarch, p.requires_obsoletes))

    def testObsoleteMultiarchToi386(self):
        p = self.pkgs
        res, msg = self.runOperation(['update'], [p.installed_i386, p.installed_x86_64], [p.obsoletes_i386])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_i386,))
    def testObsoleteMultiarchToi386ForDependency(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'superzippy'], [p.installed_i386, p.installed_x86_64], [p.obsoletes_i386, p.requires_obsoletes])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_i386, p.requires_obsoletes))

    def testObsoleteMultiarchTox86_64(self):
        p = self.pkgs
        res, msg = self.runOperation(['update'], [p.installed_i386, p.installed_x86_64], [p.obsoletes_x86_64])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_x86_64,))
    def testObsoleteMultiarchTox86_64ForDependency(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'superzippy'], [p.installed_i386, p.installed_x86_64], [p.obsoletes_x86_64, p.requires_obsoletes])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_x86_64, p.requires_obsoletes))

    def testObsoleteMultiarchToMultiarch(self):
        p = self.pkgs
        res, msg = self.runOperation(['update'], [p.installed_i386, p.installed_x86_64], [p.obsoletes_i386, p.obsoletes_x86_64])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_i386, p.obsoletes_x86_64))
    def testObsoleteMultiarchToMultiarchForDependency(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'superzippy'],
                                     [p.installed_i386, p.installed_x86_64], [p.obsoletes_i386, p.obsoletes_x86_64, p.requires_obsoletes])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_i386, p.obsoletes_x86_64, p.requires_obsoletes))


    def testInstallObsoletenoarchTonoarch(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'zsh-ng'], [p.installed_noarch], [p.obsoletes_noarch])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_noarch,))

    def testObsoletesOffPostInst1(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'zsh'], [p.obsoletes_i386], [p.installed_i386])
        self.assertTrue(res=='empty', msg)

    def testObsoletesOffPostInst2(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'zsh'], [p.obsoletes_i386], [p.installed_i386], {'obsoletes' : False})
        self.assertTrue(res=='empty', msg)

    def testObsoletesOffPostAvail1(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'zsh-ng', 'zsh'], [], [p.obsoletes_i386, p.installed_i386])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_i386,))

    def testObsoletesOffPostAvail2(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'zsh-ng', 'zsh'], [], [p.obsoletes_i386, p.installed_i386], {'obsoletes' : False})
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_i386,))

    def testObsoletesOffPostAvail3(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'zsh', 'zsh-ng'], [], [p.obsoletes_i386, p.installed_i386])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_i386,))

    def testObsoletesOffPostAvail4(self):
        p = self.pkgs
        res, msg = self.runOperation(['install', 'zsh', 'zsh-ng'], [], [p.obsoletes_i386, p.installed_i386], {'obsoletes' : False})
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.obsoletes_i386,))

    def _MultiObsHelper(self):
        ret = {'zsh'  : FakePackage('zsh', '1', '1', '0', 'noarch'),
               'ksh'  : FakePackage('ksh', '1', '1', '0', 'noarch'),
               'nash' : FakePackage('nash', '1', '1', '0', 'noarch')}
        ret['pi'] = [ret['zsh'], ret['ksh'], ret['nash']]
              
        ret['fish'] = FakePackage('fish', '0.1', '1', '0', 'noarch')
        ret['fish'].addObsoletes('zsh', None, (None, None, None))
        ret['bigfish'] = FakePackage('bigfish', '0.2', '1', '0', 'noarch')
        ret['bigfish'].addObsoletes('zsh', None, (None, None, None))
        ret['bigfish'].addObsoletes('ksh', None, (None, None, None))
        ret['shark'] = FakePackage('shark', '0.3', '1', '0', 'noarch')
        ret['shark'].addObsoletes('zsh', None, (None, None, None))
        ret['shark'].addObsoletes('ksh', None, (None, None, None))
        ret['shark'].addObsoletes('nash', None, (None, None, None))

        ret['po'] = [ret['fish'], ret['bigfish'], ret['shark']]
        return ret

    def testMultiObs1(self):
        pkgs = self._MultiObsHelper()
        res, msg = self.runOperation(['install', 'fish'],
                                     pkgs['pi'], pkgs['po'])
        self.assertTrue(res=='ok', msg)
        self.assertResult((pkgs['ksh'],pkgs['nash'],pkgs['fish'],))

    def testMultiObs2(self):
        pkgs = self._MultiObsHelper()
        res, msg = self.runOperation(['install', 'bigfish'],
                                     pkgs['pi'], pkgs['po'])
        self.assertTrue(res=='ok', msg)
        self.assertResult((pkgs['nash'],pkgs['bigfish'],))

    def testMultiObs3(self):
        pkgs = self._MultiObsHelper()
        res, msg = self.runOperation(['install', 'shark'],
                                     pkgs['pi'], pkgs['po'])
        self.assertTrue(res=='ok', msg)
        self.assertResult((pkgs['shark'],))

    def testMultiObs4(self):
        # This tests update...
        pkgs = self._MultiObsHelper()
        oldshark = FakePackage('shark', '0.1', '1', '0', 'noarch')

        res, msg = self.runOperation(['update', 'shark'],
                                     pkgs['pi'] + [oldshark], pkgs['po'])
        self.assertTrue(res=='ok', msg)
        self.assertResult((pkgs['shark'],))

    def testMultiObs5(self):
        # This tests update of the to be obsoleted pkg...
        pkgs = self._MultiObsHelper()
        oldshark = FakePackage('shark', '0.1', '1', '0', 'noarch')

        res, msg = self.runOperation(['update', 'nash'],
                                     pkgs['pi'] + [oldshark], pkgs['po'])
        self.assertTrue(res=='ok', msg)
        self.assertResult((pkgs['shark'],))

    # NOTE: Do we really want to remove the old kernel-xen? ... not 100% sure
    def testMultiObsKern1(self):
        # kernel + kernel-xen installed, and update kernel obsoletes kernel-xen
        okern1    = FakePackage('kernel',     '0.1', '1', '0', 'noarch')
        okern2    = FakePackage('kernel',     '0.2', '1', '0', 'noarch')
        okernxen1 = FakePackage('kernel-xen', '0.1', '1', '0', 'noarch')
        okernxen2 = FakePackage('kernel-xen', '0.2', '1', '0', 'noarch')
        nkern     = FakePackage('kernel',     '0.8', '1', '0', 'noarch')
        nkern.addObsoletes('kernel-xen', None, (None, None, None))

        res, msg = self.runOperation(['update', 'kernel'],
                                     [okern1, okernxen1,
                                      okern2, okernxen2], [nkern])
        self.assertTrue(res=='ok', msg)
        self.assertResult((okern1,okern2,nkern,))

    def testMultiObsKern2(self):
        # kernel + kernel-xen installed, and update kernel obsoletes kernel-xen
        okern1    = FakePackage('kernel',     '0.1', '1', '0', 'noarch')
        okern2    = FakePackage('kernel',     '0.2', '1', '0', 'noarch')
        okernxen1 = FakePackage('kernel-xen', '0.1', '1', '0', 'noarch')
        okernxen2 = FakePackage('kernel-xen', '0.2', '1', '0', 'noarch')
        nkern     = FakePackage('kernel',     '0.8', '1', '0', 'noarch')
        nkern.addObsoletes('kernel-xen', None, (None, None, None))

        res, msg = self.runOperation(['update', 'kernel-xen'],
                                     [okern1, okernxen1,
                                      okern2, okernxen2], [nkern])
        self.assertTrue(res=='ok', msg)
        self.assertResult((okern1,okern2,nkern,))

    def testMultiObsKern3(self):
        # kernel + kernel-xen installed, and update kernel obsoletes kernel-xen
        okern1    = FakePackage('kernel',     '0.1', '1', '0', 'noarch')
        okern2    = FakePackage('kernel',     '0.2', '1', '0', 'noarch')
        okernxen1 = FakePackage('kernel-xen', '0.1', '1', '0', 'noarch')
        okernxen2 = FakePackage('kernel-xen', '0.2', '1', '0', 'noarch')
        nkern     = FakePackage('kernel',     '0.8', '1', '0', 'noarch')
        nkern.addObsoletes('kernel-xen', None, (None, None, None))

        res, msg = self.runOperation(['update'],
                                     [okern1, okernxen1,
                                      okern2, okernxen2], [nkern])
        self.assertTrue(res=='ok', msg)
        self.assertResult((okern1,okern2,nkern,))

    def testIncluderObs1(self):
        #  We use an obsolete to include a new package Y for people with an
        # installed pkg X. X satisfies deps. but isn't the normal best provider
        # ... traditionally we've included the other dep. _as well_.
        #  The "main" offender has been postfix, which brings in exim.
        pfix1      = FakePackage('postfix',      '1', '1', '0', 'noarch')
        pfix1.addProvides('/usr/bin/sendmail')
        pfix2      = FakePackage('postfix',      '1', '2', '0', 'noarch')
        pfix2.addProvides('/usr/bin/sendmail')
        pnewfix    = FakePackage('postfix-blah', '1', '2', '0', 'noarch')
        pnewfix.addObsoletes('postfix', 'LT', ('0', '1', '2'))
        pnewfix.addRequires('postfix', 'EQ', ('0', '1', '2'))

        dep        = FakePackage('foo', '1', '1', '0', 'noarch')
        dep.addRequires('/usr/bin/sendmail')

        exim       = FakePackage('exim', '1', '1', '0', 'noarch')
        exim.addProvides('/usr/bin/sendmail')

        res, msg = self.runOperation(['update', 'postfix'],
                                     [pfix1, dep], [exim, pnewfix, pfix2, dep])
        self.assertTrue(res=='ok', msg)
        self.assertResult((dep, pfix2, pnewfix))

    def testIncluderObs2(self):
        #  We use an obsolete to include a new package Y for people with an
        # installed pkg X. X satisfies deps. but isn't the normal best provider
        # ... traditionally we've included the other dep. _as well_.
        #  The "main" offender has been postfix, which brings in exim.
        dep        = FakePackage('foo', '1', '1', '0', 'noarch')
        dep.addRequires('/usr/bin/sendmail')

        pfix1      = FakePackage('postfix',      '1', '1', '0', 'noarch')
        pfix1.addProvides('/usr/bin/sendmail')
        pfix2      = FakePackage('postfix',      '1', '2', '0', 'noarch')
        pfix2.addProvides('/usr/bin/sendmail')
        pnewfix    = FakePackage('postfix-blah', '1', '2', '0', 'noarch')
        pnewfix.addObsoletes('postfix', 'LT', ('0', '1', '2'))
        pnewfix.addRequires('postfix', 'EQ', ('0', '1', '2'))

        exim       = FakePackage('exim', '1', '1', '0', 'noarch')
        exim.addProvides('/usr/bin/sendmail')

        res, msg = self.runOperation(['update', 'postfix'],
                                     [dep, pfix1], [dep, pfix2, pnewfix, exim])
        self.assertTrue(res=='ok', msg)
        self.assertResult((dep, pfix2, pnewfix))

    def testIncluderObs3(self):
        #  We use an obsolete to include a new package Y for people with an
        # installed pkg X. X satisfies deps. but isn't the normal best provider
        # ... traditionally we've included the other dep. _as well_.
        #  The "main" offender has been postfix, which brings in exim.
        dep        = FakePackage('foo', '1', '1', '0', 'noarch')
        dep.addRequires('/usr/bin/sendmail')

        pfix1      = FakePackage('postfix',      '1', '1', '0', 'noarch')
        pfix1.addProvides('/usr/bin/sendmail')
        pfix2      = FakePackage('postfix',      '1', '2', '0', 'noarch')
        pfix2.addProvides('/usr/bin/sendmail')
        pnewfix    = FakePackage('postfix-blah', '1', '2', '0', 'noarch')
        pnewfix.addObsoletes('postfix', 'LT', ('0', '1', '2'))
        pnewfix.addRequires('postfix', 'EQ', ('0', '1', '2'))

        exim       = FakePackage('exim', '1', '1', '0', 'noarch')
        exim.addProvides('/usr/bin/sendmail')

        res, msg = self.runOperation(['install', 'postfix-blah'],
                                     [dep, pfix1], [dep, pfix2, pnewfix, exim])
        self.assertTrue(res=='ok', msg)
        self.assertResult((dep, pfix2, pnewfix))

    def testConflictMultiplePkgs(self):
        rp1        = FakePackage('foo', '1', '1', '0', 'noarch')

        aop        = FakePackage('bar', '1', '1', '0', 'noarch')
        aop.addObsoletes('foo', 'LT', ('0', '1', '2'))
        ap         = FakePackage('baz', '1', '1', '0', 'noarch')
        ap.addRequires('d1')
        ap.addRequires('d2')
        ap.addRequires('d3')

        dep1        = FakePackage('d1', '1', '1', '0', 'noarch')
        dep1.addConflicts('foo', 'LT', ('0', '1', '2'))
        dep2        = FakePackage('d2', '1', '1', '0', 'noarch')
        dep2.addConflicts('foo', 'LT', ('0', '1', '2'))
        dep3        = FakePackage('d3', '1', '1', '0', 'noarch')
        dep3.addConflicts('foo', 'LT', ('0', '1', '2'))

        res, msg = self.runOperation(['install', 'baz'],
                                     [rp1], [ap, aop, dep1, dep2, dep3])
        self.assertTrue(res=='ok', msg)
        self.assertResult((ap, aop, dep1, dep2, dep3))

    def testMultipleObsoleters(self):
        rp1        = FakePackage('foo', '1', '1', '0', 'noarch')

        aop1        = FakePackage('bar', '1', '1', '0', 'noarch')
        aop1.addObsoletes('foo', 'LT', ('0', '1', '2'))
        aop1.addConflicts('bazing')
        aop2        = FakePackage('bazing', '1', '1', '0', 'noarch')
        aop2.addObsoletes('foo', 'LT', ('0', '1', '2'))
        aop2.addConflicts('bar')

        res, msg = self.runOperation(['update'],
                                     [rp1], [aop1, aop2])
        self.assertTrue(res=='err', msg)
        # FIXME: This is really what should happen, but just sucking works too
        # self.assert_(res=='ok', msg)
        # self.assertResult((aop1,))

    def _helperRLDaplMess(self):
        rp1 = FakePackage('dapl',       '1.2.1', '7', arch='i386')
        rp2 = FakePackage('dapl-devel', '1.2.1', '7', arch='i386')
        rp2.addRequires('dapl', 'EQ', ('0', '1.2.1', '7'))

        arp1 = FakePackage('dapl',       '1.2.1.1', '7', arch='i386')
        arp2 = FakePackage('dapl-devel', '1.2.1.1', '7', arch='i386')
        arp2.addRequires('dapl', 'EQ', ('0', '1.2.1.1', '7'))
        arp3 = FakePackage('dapl',       '2.0.15', '1.el4', arch='i386')
        arp4 = FakePackage('dapl-devel', '2.0.15', '1.el4', arch='i386')
        arp4.addRequires('dapl', 'EQ', ('0', '2.0.15', '1.el4'))

        aop1 = FakePackage('compat-dapl-1.2.5', '2.0.7', '2.el4', arch='i386')
        aop1.addObsoletes('dapl', 'LE', (None, '1.2.1.1', None))
        aop2 = FakePackage('compat-dapl-devel-1.2.5', '2.0.7', '2.el4',
                           arch='i386')
        aop2.addObsoletes('dapl-devel', 'LE', (None, '1.2.1.1', None))
        aop2.addRequires('dapl', 'EQ', ('0', '2.0.7', '2.el4'))

        aoop1 = FakePackage('compat-dapl', '2.0.15', '1.el4', arch='i386')
        aoop1.addObsoletes('dapl', 'LE', (None, '1.2.1.1', None))
        aoop1.addObsoletes('compat-dapl-1.2.5', None, (None, None, None))
        aoop2 = FakePackage('compat-dapl-devel', '2.0.15', '1.el4', arch='i386')
        aoop2.addObsoletes('dapl-devel', 'LE', (None, '1.2.1.1', None))
        aoop2.addObsoletes('compat-dapl-devel-1.2.5', None, (None, None, None))
        aoop2.addRequires('compat-dapl', 'EQ', ('0', '2.0.15', '1.el4'))

        return [rp1, rp2], [arp1, arp2, arp3, arp4,
                            aop1, aop2, aoop1, aoop2], [aoop1, aoop2], locals()

    def testRLDaplMess1(self):
        rps, aps, ret, all = self._helperRLDaplMess()
        res, msg = self.runOperation(['update'], rps, aps)

        self.assertTrue(res=='ok', msg)
        self.assertResult(ret)

    def testRLDaplMess2(self):
        rps, aps, ret, all = self._helperRLDaplMess()
        res, msg = self.runOperation(['update', 'dapl'], rps, aps)

        self.assertTrue(res=='ok', msg)
        self.assertResult(ret)

    def testRLDaplMess3(self):
        rps, aps, ret, all = self._helperRLDaplMess()
        res, msg = self.runOperation(['update', 'dapl-devel'], rps, aps)

        self.assertTrue(res=='ok', msg)
        self.assertResult(ret)

    def testRLDaplMess4(self):
        rps, aps, ret, all = self._helperRLDaplMess()
        res, msg = self.runOperation(['install', 'compat-dapl'], rps, aps)

        self.assertTrue(res=='ok', msg)
        self.assertResult(ret)

    def testRLDaplMess5(self):
        rps, aps, ret, all = self._helperRLDaplMess()
        res, msg = self.runOperation(['install', 'compat-dapl-devel'], rps, aps)

        self.assertTrue(res=='ok', msg)
        self.assertResult(ret)

    def testRLDaplMess6(self):
        rps, aps, ret, all = self._helperRLDaplMess()
        res, msg = self.runOperation(['install', 'compat-dapl-1.2.5'], rps, aps)

        self.assertTrue(res=='ok', msg)
        self.assertResult(ret)

    def testRLDaplMess7(self):
        rps, aps, ret, all = self._helperRLDaplMess()
        res, msg = self.runOperation(['install', 'compat-dapl-devel-1.2.5'],
                                     rps, aps)

        self.assertTrue(res=='ok', msg)
        self.assertResult(ret)

    # Now we get a bit weird, as we have obsoletes fighting with updates
    def testRLDaplMessWeirdInst1(self):
        rps, aps, ret, all = self._helperRLDaplMess()
        res, msg = self.runOperation(['install', 'dapl-1.2.1.1-7'], rps, aps)

        self.assertTrue(res=='ok', msg)
        self.assertResult(ret)
    def testRLDaplMessWeirdInst2(self):
        rps, aps, ret, all = self._helperRLDaplMess()
        res, msg = self.runOperation(['install', 'dapl-2.0.15',
                                      'dapl-devel-2.0.15'], rps, aps)

        self.assertTrue(res=='ok', msg)
        self.assertResult((all['arp3'], all['arp4']))
    def testRLDaplMessWeirdInst3(self):
        rps, aps, ret, all = self._helperRLDaplMess()
        res, msg = self.runOperation(['install', 'dapl-2.0.15'], rps, aps)

        self.assertTrue(res=='ok', msg)
        self.assertResult((all['arp3'], all['arp4']))
    def testRLDaplMessWeirdUp1(self):
        rps, aps, ret, all = self._helperRLDaplMess()
        res, msg = self.runOperation(['update', 'dapl-1.2.1.1-7'], rps, aps)

        self.assertTrue(res=='ok', msg)
        self.assertResult(ret)
    def testRLDaplMessWeirdUp2(self):
        rps, aps, ret, all = self._helperRLDaplMess()
        res, msg = self.runOperation(['update', 'dapl-2.0.15',
                                      'dapl-devel-2.0.15'], rps, aps)

        self.assertTrue(res=='ok', msg)
        self.assertResult((all['arp3'], all['arp4']))
    def testRLDaplMessWeirdUp3(self):
        rps, aps, ret, all = self._helperRLDaplMess()
        res, msg = self.runOperation(['update', 'dapl-2.0.15'], rps, aps)

        self.assertTrue(res=='ok', msg)
        self.assertResult((all['arp3'], all['arp4']))

    def testRLDaplFixUpdateNotInstall(self):
        rps, aps, ret, all = self._helperRLDaplMess()
        res, msg = self.runOperation(['update', 'dapl-1.2.1*'], [], rps + aps)

        # self.assert_(res=='err', msg)
        self.assertResult([])

    def testRLOpenSSLMess1(self):
        osl1  = FakePackage('openssl',      '1.0.0', '1', arch='i386')
        osl1.addProvides('libssl.1', 'EQ', ('0', '1', '1'))
        osl2  = FakePackage('openssl',      '1.0.1', '1', arch='i386')
        osll2 = FakePackage('openssl-libs', '1.0.1', '1', arch='i386')
        osll2.addProvides('libssl.2', 'EQ', ('0', '2', '1'))
        osll2.addObsoletes('openssl', 'LT', (None, '1.0.1', None))

        oc1   = FakePackage('openconnect',  '2.0.1', '1', arch='i386')
        oc1.addRequires('openssl', 'GE', ('0', '0.9.9', '1'))
        oc2   = FakePackage('openconnect',  '2.0.2', '1', arch='i386')
        oc2.addRequires('openssl', 'GE', ('0', '0.9.9', '1'))

        res, msg = self.runOperation(['upgrade', 'openssl'],
                                     [oc1, osl1],
                                     [oc1, oc2, osl1, osl2, osll2])

        # In theory don't need to upgrade oc1 => oc2
        self.assertResult((oc2, osl2, osll2))

    def testCircObs1(self):
        c1 = FakePackage('test-ccc', '0.1', '1')
        c2 = FakePackage('test-ccc', '0.2', '2')
        c2.addObsoletes('test-ddd', None, (None, None, None))

        d1 = FakePackage('test-ddd', '0.1', '1')
        d2 = FakePackage('test-ddd', '0.2', '2')
        d2.addObsoletes('test-ccc', None, (None, None, None))

        res, msg = self.runOperation(['upgrade'],
                                     [c1, d1],
                                     [c1, d1, c2, d2])

        self.assertResult((c2, d2))

    def testCircObs2(self):
        c1 = FakePackage('test-ccc', '0.1', '1')
        c2 = FakePackage('test-ccc', '0.2', '2')
        c2.addObsoletes('test-ddd', None, (None, None, None))

        d1 = FakePackage('test-ddd', '0.1', '1')
        d2 = FakePackage('test-ddd', '0.2', '2')
        d2.addObsoletes('test-ccc', None, (None, None, None))

        res, msg = self.runOperation(['upgrade', 'test-ccc', 'test-ddd'],
                                     [c1, d1],
                                     [c1, d1, c2, d2])

        self.assertResult((c2, d2))

    def testCircObs3(self):
        c1 = FakePackage('test-ccc', '0.1', '1')
        c2 = FakePackage('test-ccc', '0.2', '2')
        c2.addObsoletes('test-ddd', None, (None, None, None))

        d1 = FakePackage('test-ddd', '0.1', '1')
        d2 = FakePackage('test-ddd', '0.2', '2')
        d2.addObsoletes('test-ccc', None, (None, None, None))

        res, msg = self.runOperation(['upgrade', 'test-ccc'],
                                     [c1, d1],
                                     [c1, d1, c2, d2])

        # Just c2 is fine too, although less likely what the user wants
        self.assertResult((c2,d2))

    def testCircObs4(self):
        c1 = FakePackage('test-ccc', '0.1', '1')
        c2 = FakePackage('test-ccc', '0.2', '2')
        c2.addObsoletes('test-ddd', None, (None, None, None))

        d1 = FakePackage('test-ddd', '0.1', '1')
        d2 = FakePackage('test-ddd', '0.2', '2')
        d2.addObsoletes('test-ccc', None, (None, None, None))

        res, msg = self.runOperation(['upgrade', 'test-ddd'],
                                     [c1, d1],
                                     [c1, d1, c2, d2])

        # Just d2 is fine too, although less likely what the user wants
        self.assertResult((c2,d2))

    def testRLFileReqTransObs1(self):
        fr1 = FakePackage('fr1', '1', '1')
        fr1.addRequires('/foo')
        fr2 = FakePackage('fr2', '2', '2')

        fp1 = FakePackage('fp1', '1', '2')
        fp1.addFile('/foo')
        fp2 = FakePackage('fpl2', '1', '2')
        fp2.addFile('/foo')

        ob1 = FakePackage('ob1', '1', '3')
        ob1.addObsoletes('fp1', None, (None, None, None))

        res, msg = self.runOperation(['install', 'ob1', 'fr1'], [],
                                     [fr1, fr2, fp1, fp2, ob1])

        self.assertTrue(res=='err', msg)
        # Should really be:
        # self.assertResult([ob1, fr1, fp2])

    def testRLFileReqTransObs2(self):
        fr1 = FakePackage('fr1', '1', '1')
        fr1.addRequires('/foo')
        fr2 = FakePackage('fr2', '2', '2')
        fr2.addRequires('/bar')

        fp1 = FakePackage('fp1', '1', '2')
        fp1.addFile('/foo')
        fp2 = FakePackage('fpl2', '1', '2')
        fp2.addFile('/foo')

        ob1 = FakePackage('ob1', '1', '3')
        ob1.addObsoletes('fp1', None, (None, None, None))
        ob1.addFile('/bar')

        res, msg = self.runOperation(['install', 'fr1', 'fr2'], [],
                                     [fr1, fr2, fp1, fp2, ob1])

        self.assertTrue(res=='err', msg)
        # Should really be:
        # self.assertResult([ob1, fr1, fp2])

    def testRLFileReqInstObs(self):
        fr1 = FakePackage('fr1', '1', '1')
        fr1.addRequires('/foo')
        fr2 = FakePackage('fr2', '2', '2')

        fp1 = FakePackage('fp1', '1', '2')
        fp1.addFile('/foo')
        fp2 = FakePackage('fpl2', '1', '2')
        fp2.addFile('/foo')

        ob1 = FakePackage('ob1', '1', '3')
        ob1.addObsoletes('fp1', None, (None, None, None))

        res, msg = self.runOperation(['install', 'fr1'], [ob1],
                                     [fr1, fr2, fp1, fp2, ob1])
        print("JDBG:", "test:", res, msg)

        self.assertTrue(res=='err', msg)
        # Should really be:
        # self.assertResult([ob1, fr1, fp2])


class GitMetapackageObsoletesTests(OperationsTests):

    @staticmethod
    def buildPkgs(pkgs, *args):
        # installed
        pkgs.installed = FakePackage('git-core', '1.5.4.2', '1', '0', 'x86_64')
        pkgs.metapackage = FakePackage('git', '1.5.4.2', '1', '0', 'x86_64')
        # obsoletes
        pkgs.new_git = FakePackage('git', '1.5.4.4', '1', '0', 'x86_64')
        pkgs.new_git.addObsoletes('git-core', 'LE', ('0', '1.5.4.3', '1'))
        pkgs.new_git.addProvides('git-core', 'EQ', ('0', '1.5.4', '1'))

        pkgs.git_all = FakePackage('git-all', '1.5.4', '1', '0', 'x86_64')
        pkgs.git_all.addObsoletes('git', 'LE', ('0', '1.5.4.3', '1'))


    def testGitMetapackageOnlyCoreInstalled(self):
        # Fedora had a package named 'git', which was a metapackage requiring
        # all other git rpms. Most people wanted 'git-core' when they asked for
        # git, so we renamed them.
        # git-core became git, and provided git-core = version while obsoleting
        # git-core < version
        # git became git-all, obsoleting git < version

        p = self.pkgs
        res, msg = self.runOperation(['update'], [p.installed],
                [p.new_git, p.git_all])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.new_git,))

    def testGitMetapackageRenameMetapackageAndCoreInstalled(self):
        p = self.pkgs
        res, msg = self.runOperation(['update'], [p.installed, p.metapackage],
                [p.new_git, p.git_all])
        self.assertTrue(res=='ok', msg)
        self.assertResult((p.new_git, p.git_all))
