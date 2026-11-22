"""Independent export mutation, completeness and portable replay regressions."""
import asyncio
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tools.rooms_v4_capacity import run
from tools.rooms_v4_export import export, file_sha, verify_export
from tools.rooms_v4_independent_decode import independent_patch, canonical, iter_decode
from tools.rooms_v4_portable_audit import audit


class ExportIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name)/'fixture'
        asyncio.run(run(cls.root,days=1,mode='files',generate_only=True))
        export(cls.root/'research',cls.root/'export',run_id='fixture',source_commit='fixture')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def copy_export(self,root):
        target=Path(root)/'export';shutil.copytree(self.root/'export',target);return target

    def test_source_record_mutation_at_pack_creation_does_not_publish_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'research';shutil.copytree(self.root/'research',source)
            original=tarfile.open;changed=[]
            def opening(name,*args,**kwargs):
                result=original(name,*args,**kwargs)
                if not changed and kwargs.get('mode',args[0] if args else None)=='w':
                    p=source/'records/00000000.json.gz'
                    raw=bytearray(p.read_bytes());raw[-1]^=1;p.write_bytes(raw);changed.append(True)
                return result
            out=Path(tmp)/'export'
            with patch('tools.rooms_v4_export.tarfile.open',side_effect=opening):
                with self.assertRaisesRegex(ValueError,'record changed during export'):
                    export(source,out,run_id='fixture',source_commit='fixture')
            self.assertTrue(changed);self.assertFalse((out/'export.json').exists())

    def test_empty_manifest_and_omitted_pack_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=self.copy_export(tmp);path=out/'export.json';original=json.loads(path.read_text())
            path.write_text(json.dumps({'files':{},'run_id':'unverified'}))
            with self.assertRaises(ValueError):verify_export(out)
            changed=json.loads(json.dumps(original));name=changed['packs'].pop()['file'];del changed['files'][name]
            path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError,'coverage'):verify_export(out)

    def test_inner_record_corruption_with_rehashed_outer_pack_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=self.copy_export(tmp);pack=out/'segment-00000.tar'
            with tarfile.open(pack) as tar:
                contents=[(m.name,tar.extractfile(m).read()) for m in tar]
            with tarfile.open(pack,'w',format=tarfile.USTAR_FORMAT) as tar:
                for name,data in contents:
                    if name=='records/00000000.json.gz':data=data[:-1]+bytes([data[-1]^1])
                    info=tarfile.TarInfo(name);info.size=len(data);tar.addfile(info,io.BytesIO(data))
            path=out/'export.json';manifest=json.loads(path.read_text())
            meta={'bytes':pack.stat().st_size,'sha256':file_sha(pack)}
            manifest['files'][pack.name]=meta;manifest['packs'][0].update(meta);path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError,'record checksum'):verify_export(out)

    def test_rehashed_truncated_chronological_index_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=self.copy_export(tmp);events=out/'events.jsonl';events.write_text('')
            path=out/'export.json';manifest=json.loads(path.read_text())
            manifest['files'][events.name]={'bytes':0,'sha256':file_sha(events)}
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError,'chronological index is incomplete'):verify_export(out)

    def test_portable_restore_and_independent_file_reads_revisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'audited';shutil.copytree(self.root,root)
            shutil.rmtree(root/'export')
            report=audit(root,run_id='fixture',source_commit='fixture',mode='files')
            self.assertEqual(report['independent_decoder']['decisions'],9)
            self.assertEqual(report['file_policy']['exact_daily_writes'],3)
            self.assertEqual(report['file_policy']['exact_daily_reads'],3)
            self.assertEqual(report['independent_decoder']['artifact_revisions'],3)
            self.assertEqual(report['independent_decoder']['complete_histories'],{'A':3,'B':3,'C':3})
            metrics={};decoded=list(iter_decode(root/'reassembled',metrics=metrics))
            self.assertEqual(len(decoded),report['independent_domain']['independent_domain_events_verified'])
            for a,b in ((True,1),(-0.0,0.0),({'x':[1]},{'x':[1,True]})):
                self.assertEqual(canonical(independent_patch(a,['=',b])),canonical(b))

if __name__=='__main__':
    unittest.main()
