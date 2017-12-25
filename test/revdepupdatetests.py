from testbase import *

class RevdepUpdateTests(OperationsTests):

    @staticmethod
    def buildPkgs(pkgs, *args):
        """ This test checks that remove_old_deps handles reverse 
        dependencies properly during yum update. Specifically, 
        remove_old_deps should remove precisely the packages which are not 
        required by any package currently installed or pending 
        installation. Two cases:
        
        For packages A, B, we write A -> B if B requires A. Packages 
        with a dep prefix are dep-installed.

        1) Installed: dep1 -> dep2 -> pkg1 <- dep3 and dep3 -> dep2
           Update: pkg1, which requires dep2 but no longer requires dep3.
           Correct outcome: dep1, dep2, dep3, pkg1, since dep2 still 
           needs dep3.

        2) Installed: dep1 -> dep2 -> pkg1 <- dep3 and dep3 -> dep2
           Update: pkg1, which now requires only dep1
           Correct outcome: dep1, pkg1, since dep2 and dep3 are 
           no longer needed.
           
        """

        
        pkgs.installed_1 = FakePackage('dep1', '1', '0', '0', 'noarch')
        pkgs.installed_1.yumdb_info.reason = 'dep'
        
        pkgs.installed_2 = FakePackage('dep2', '1', '0', '0', 'noarch')
        pkgs.installed_2.yumdb_info.reason = 'dep'
        
        pkgs.installed_3 = FakePackage('pkg1', '1', '0', '0', 'noarch')
        pkgs.installed_3.yumdb_info.reason = 'user'

        pkgs.installed_4 = FakePackage('dep3', '1', '0', '0', 'noarch')
        pkgs.installed_4.yumdb_info.reason = 'dep'
        
        pkgs.installed_1.addRequiringPkg(pkgs.installed_2)
        pkgs.installed_2.addRequiringPkg(pkgs.installed_3)
        pkgs.installed_4.addRequiringPkg(pkgs.installed_2)

        pkgs.installed_2.addRequiresPkg(pkgs.installed_1)
        pkgs.installed_2.addRequiresPkg(pkgs.installed_4)
        pkgs.installed_3.addRequiresPkg(pkgs.installed_4)
        pkgs.installed_3.addRequiresPkg(pkgs.installed_2)

        pkgs.update_2 = FakePackage('dep2', '2', '0', '0', 'noarch')
        pkgs.update_2.addRequires('dep1', 'EQ', ('0', '1', '0'))

        pkgs.update_3 = FakePackage('pkg1', '2', '0', '0', 'noarch')
        pkgs.update_3.addRequires('dep2', 'EQ', ('0', '1', '0'))

        pkgs.update_4 = FakePackage('pkg1', '2', '0', '0', 'noarch')
        pkgs.update_4.addRequires('dep1', 'EQ', ('0', '1', '0'))

    def testUpdate(self):
        p = self.pkgs
        res, msg = self.runOperation(['update'], [p.installed_1, p.installed_2, p.installed_3, p.installed_4], [p.update_3])
        self.assertTrue(res=='ok', msg)
        self.assertResult( (p.installed_1, p.installed_2, p.update_3, p.installed_4) )
    
    def testUpdate2(self):
        p = self.pkgs
        res, msg = self.runOperation(['update'], [p.installed_1, p.installed_2, p.installed_3, p.installed_4], [p.update_4])
        self.assertTrue(res=='ok', msg)
        self.assertResult( (p.installed_1, p.update_4) )

