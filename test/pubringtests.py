import unittest
import gpg
import os
import shutil
import tempfile

from yum import misc

PWD = os.path.dirname(os.path.abspath(__file__))
KEYDIR = '%s/gpg' % PWD
KEYFILE = '%s/key.pub' % KEYDIR
REPOMD = '%s/repomd.xml' % KEYDIR
SIGFILE = '%s/repomd.xml.asc' % KEYDIR
KEYID = '38BB1B5ED5865417'
FPR = '417A0E6E55566A755BE7D68C38BB1B5ED5865417'

class PubringTests(unittest.TestCase):
    def setUp(self):
        self.gpgdir = tempfile.mkdtemp()
        with open(KEYFILE) as f:
            info = misc.getgpgkeyinfo(f.read())
        hexkeyid = misc.keyIdToRPMVer(info['keyid']).upper()
        self.imported = misc.import_key_to_pubring(info['raw_key'], hexkeyid,
                                                   gpgdir=self.gpgdir)
        self.ctx = gpg.Context()

    def tearDown(self):
        shutil.rmtree(self.gpgdir)

    def testImportKey(self):
        self.assertTrue(self.imported)
        key = list(self.ctx.keylist())[0].subkeys[0]
        self.assertEqual(key.fpr, FPR)

    def testKeyids(self):
        ids = misc.return_keyids_from_pubring(self.gpgdir)
        self.assertEqual(ids[0], KEYID)
        self.assertTrue(len(ids) == 1)

    def testValid(self):
        with open(SIGFILE) as s, open(REPOMD) as r:
            valid = misc.valid_detached_sig(s, r, self.gpgdir)
        self.assertTrue(valid)
