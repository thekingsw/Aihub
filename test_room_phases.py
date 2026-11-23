import json, tempfile, unittest
from pathlib import Path
from room_dispatch import Dispatch, brief_report

class PhaseTests(unittest.TestCase):
    def test_two_phases_and_review_gate(self):
        with tempfile.TemporaryDirectory(dir='.') as temp:
            d=Dispatch(Path(temp)/'test.db')
            def event(sender, body):
                mid=str(int(d.cursor() or '0')+1)
                d.ingest(d.cursor(),[dict(id=mid,body=body,**{'from':sender},kind='handoff',agent_turns=1,ref='sample-report.md')])
                with d.transaction() as db:
                    return d.get(db,'phase:sample',None)
            p=event('emi','/phase plan sample ["1+1=2", "2+2=4"]\nEm đã rõ, đây là kế hoạch hai bước.')
            self.assertEqual(p['current'],1)
            event('tinker','/phase pass sample 1') # premature pass rejected
            p=event('emi','/phase done sample 1\nKết quả 1+1=2; chi tiết sample-report.md')
            self.assertEqual(p['state'],'review')
            p=event('emi','/phase done sample 2')
            self.assertEqual(p['current'],1)
            p=event('tinker','/phase fail sample 1\nCần bằng chứng')
            self.assertEqual(p['current'],1)
            event('emi','/phase done sample 1')
            p=event('tinker','/phase pass sample 1\nĐã đối chiếu 1+1=2')
            self.assertEqual(p['current'],2)
            event('emi','/phase done sample 2')
            p=event('tinker','/phase pass sample 2\nĐã đối chiếu 2+2=4')
            self.assertEqual(p['state'],'complete')
            self.assertEqual(Dispatch(d.path).cursor(),d.cursor())
    def test_dump_shortening(self):
        text='```diff\n'+'+ technical detail\n'*1000+'```'
        out=brief_report(text,'emi-report-sample.md')
        self.assertLess(len(out),5000)
        self.assertNotIn('technical detail',out)
        self.assertIn('emi-report-sample.md',out)

if __name__=='__main__': unittest.main()
