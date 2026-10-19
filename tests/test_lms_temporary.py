"""Private scratch candidate only. No live services, records or credentials."""
import os
from pathlib import Path
import tempfile
import unittest
import sys
import subprocess
import json
import csv
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from services.lms_temporary import private_scratch, SCRATCH_ROOT, PROJECT_ROOT


class TemporaryReadingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent,prefix='quiz-test-temp-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'private'

    def test_default_location_is_project_relative_not_machine_specific(self):
        self.assertEqual(SCRATCH_ROOT,PROJECT_ROOT/'.private-lms'/'temporary-downloads')

    def test_jobs_unique_and_cleanup_does_not_touch_siblings(self):
        self.root.mkdir();sentinel=self.root/'unrelated.txt';sentinel.write_text('keep')
        with private_scratch(self.root) as first:
            with private_scratch(self.root) as second:
                self.assertNotEqual(first.directory,second.directory)
                first.target.write_bytes(b'partial')
                saved=second.directory
            self.assertFalse(saved.exists());self.assertTrue(first.target.exists())
        self.assertEqual(sentinel.read_text(),'keep')
        self.assertEqual(list(self.root.iterdir()),[sentinel])

    def test_exception_removes_only_its_job_and_partial_files(self):
        with self.assertRaisesRegex(RuntimeError,'synthetic'):
            with private_scratch(self.root) as job:
                folder=job.directory
                (folder/'.download-synthetic.part').write_bytes(b'partial')
                raise RuntimeError('synthetic')
        self.assertFalse(folder.exists())

    def test_permission_failure_stops_before_yield_and_leaves_no_job(self):
        with patch('services.lms_temporary.protect',side_effect=ValueError('synthetic-permissions')):
            with self.assertRaises(ValueError):
                with private_scratch(self.root):self.fail('Must not start download')
        self.assertEqual(list(self.root.iterdir()),[])

    def test_second_permission_failure_cleans_new_job(self):
        with patch('services.lms_temporary.protect',side_effect=[None,ValueError('synthetic')]):
            with self.assertRaises(ValueError):
                with private_scratch(self.root):self.fail('Must not start download')
        self.assertEqual(list(self.root.iterdir()),[])

    def test_rejects_non_pdf_without_extraction(self):
        with private_scratch(self.root) as job:
            job.target.write_bytes(b'not a PDF')
            with self.assertRaises(ValueError):job.extract_pdf()

    def test_missing_output_is_not_success(self):
        with private_scratch(self.root) as job:
            with self.assertRaises(FileNotFoundError):job.extract_pdf()

    def test_oversized_output_rejected_before_reading(self):
        with private_scratch(self.root) as job:
            with job.target.open('wb') as f:f.truncate(20*1024*1024+1)
            with self.assertRaises(ValueError):job.extract_pdf()

    def test_external_hardlink_rejected_and_original_preserved(self):
        external=Path(self.temp.name)/'original.pdf';external.write_bytes(b'%PDF-synthetic')
        with private_scratch(self.root) as job:
            os.link(external,job.target)
            with self.assertRaises(ValueError):job.extract_pdf()
        self.assertEqual(external.read_bytes(),b'%PDF-synthetic')

    @unittest.skipIf(os.name=='nt','Windows symlink creation requires developer-mode privileges')
    def test_symlink_parent_denied_without_touching_destination(self):
        outside=Path(self.temp.name)/'outside';outside.mkdir()
        self.root.symlink_to(outside,target_is_directory=True)
        with self.assertRaises(ValueError):
            with private_scratch(self.root):self.fail('Must not yield')
        self.assertEqual(list(outside.iterdir()),[])

    def test_cleanup_does_not_follow_inner_link(self):
        outside=Path(self.temp.name)/'original.pdf';outside.write_bytes(b'%PDF-synthetic')
        with private_scratch(self.root) as job:
            os.link(outside,job.directory/'other.pdf')
        self.assertEqual(outside.read_bytes(),b'%PDF-synthetic')

    @unittest.skipUnless(os.name=='nt','Windows junction contract')
    def test_windows_junction_parent_denied(self):
        import _winapi
        outside=Path(self.temp.name)/'outside';outside.mkdir()
        original=outside/'keep.txt';original.write_text('untouched')
        _winapi.CreateJunction(str(outside),str(self.root))
        with self.assertRaises(ValueError):
            with private_scratch(self.root):self.fail('Must not yield')
        self.assertEqual(original.read_text(),'untouched')
        self.assertEqual(list(outside.iterdir()),[original])

    @unittest.skipUnless(os.name=='nt','Windows junction cleanup contract')
    def test_windows_inner_junction_is_not_followed_during_cleanup(self):
        import _winapi
        outside=Path(self.temp.name)/'outside';outside.mkdir()
        original=outside/'keep.txt';original.write_text('untouched')
        with private_scratch(self.root) as job:
            _winapi.CreateJunction(str(outside),str(job.directory/'junction'))
        self.assertEqual(original.read_text(),'untouched')
        self.assertEqual(list(outside.iterdir()),[original])

    @unittest.skipUnless(os.name=='nt','Windows private ACL contract')
    def test_windows_job_acl_only_current_identity_and_system(self):
        with private_scratch(self.root) as job:
            literal=str(job.directory).replace("'","''")
            script=("$a=Get-Acl -LiteralPath '"+literal+"'; "
                    "@{protected=$a.AreAccessRulesProtected; rules=@($a.Access | ForEach-Object {"
                    "@{sid=$_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value; "
                    "inherited=$_.IsInherited; type=$_.AccessControlType.ToString(); rights=$_.FileSystemRights.ToString()}"
                    "})} | ConvertTo-Json -Depth 4 -Compress")
            probe=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
                                 capture_output=True,text=True,check=True)
            acl=json.loads(probe.stdout)
            owner=subprocess.run(['whoami.exe','/user','/fo','csv','/nh'],capture_output=True,text=True,check=True)
            sid=next(csv.reader([owner.stdout.strip()]))[1]
            self.assertTrue(acl['protected'])
            self.assertEqual({r['sid'] for r in acl['rules']},{sid,'S-1-5-18'})
            self.assertTrue(all(not r['inherited'] and r['type']=='Allow' and 'FullControl' in r['rights'] for r in acl['rules']))


if __name__=='__main__':unittest.main()
