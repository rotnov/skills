"""Native session delivery; synthetic local credentials, never a model process."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

SCRIPTS = Path(__file__).resolve().parents[1]/'skills/quorum/scripts'
sys.path.insert(0, str(SCRIPTS))
SID = '11111111-1111-4111-8111-111111111111'
MID = '22222222-2222-4222-8222-222222222222'


class ClaudePipeTests(unittest.TestCase):
    def test_foreign_session_and_remote_pipe_cannot_be_selected(self):
        from quorum_claude_pipe import binding
        values = {'CLAUDE_CODE_SESSION_ID':SID, 'CLAUDE_CODE_MESSAGING_TOKEN':'synthetic-only'}
        with patch.dict(os.environ, values, clear=True):
            with self.assertRaisesRegex(ValueError,'session_binding_denied'): binding(MID)
            for endpoint in ('\\\\server\\pipe\\remote', 'C:\\ordinary-file', '\\\\.\\pipe\\name\nextra'):
                with patch.dict(os.environ, {'CLAUDE_CODE_MESSAGING_SOCKET':endpoint}):
                    with self.assertRaisesRegex(ValueError,'claude_pipe_unavailable'): binding(SID)

    def test_wire_frames_are_scoped_and_never_assert_a_permission_class(self):
        from quorum_claude_pipe import frames
        lines = frames(SID, MID, 'Привет\nnext line', 'synthetic-only').splitlines()
        self.assertEqual(json.loads(lines[0]), {'type':'auth','token':'synthetic-only'})
        event = json.loads(lines[1])
        self.assertEqual(event, {'type':'user','session_id':SID,'from':'quorum','msg_id':MID,
                                'message':{'role':'user','content':'Привет\nnext line'}})

    def test_claude_envelope_records_actual_chat_receipt_before_answering(self):
        from quorum_session import enqueue
        seen=[]
        endpoint='\\\\.\\pipe\\synthetic-quorum'
        env={'CLAUDE_CODE_SESSION_ID':SID,'CLAUDE_CODE_MESSAGING_SOCKET':endpoint}
        with patch.dict(os.environ,env,clear=True), patch('quorum_claude_pipe.submit',side_effect=lambda *args:seen.append(args)):
            result=enqueue({'engine':'claude','session_ref':SID,'messaging_socket':endpoint},
                           {'id':MID,'body':'untrusted request','reply_to':None},Path('/private/state'))
        self.assertEqual(result,'submitted')
        self.assertEqual(seen[0][:2],(SID,MID))
        prompt=seen[0][2]
        receipt=json.loads(prompt.split('(argument array): ',1)[1].split('\n',1)[0])
        self.assertEqual(receipt[-2:],['received',MID])
        self.assertNotIn('reply',receipt)
        self.assertIn('untrusted peer',prompt)

    def test_secret_is_not_in_child_arguments_and_timeout_is_not_retried(self):
        from quorum_claude_pipe import submit
        calls=[]
        def timeout(command,**kwargs):
            calls.append((command,kwargs))
            raise subprocess.TimeoutExpired(command,10)
        with patch('quorum_claude_pipe.subprocess.run',side_effect=timeout):
            with self.assertRaisesRegex(ValueError,'session_delivery_unknown'):
                submit(SID,MID,'test message')
        self.assertEqual(len(calls),1)
        args,kwargs=calls[0]
        self.assertEqual(Path(args[1]).name,'quorum_claude_pipe.py')
        self.assertEqual(json.loads(kwargs['input'])['session_id'],SID)
        self.assertNotIn('token',kwargs['input'])
        self.assertNotIn('claude',args)

    @unittest.skipUnless(os.name=='nt','native Windows named-pipe integration')
    def test_real_windows_pipe_accepts_auth_and_exact_session_frame(self):
        from quorum_claude_pipe import submit
        name='quorum-synthetic-'+uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix='pipe fixture ') as root:
            ready=Path(root)/'ready'
            script=Path(root)/'server.ps1'
            script.write_text('''param($PipeName,$Ready)
$ErrorActionPreference='Stop'
$pipe=[IO.Pipes.NamedPipeServerStream]::new($PipeName,[IO.Pipes.PipeDirection]::InOut,1,[IO.Pipes.PipeTransmissionMode]::Byte,[IO.Pipes.PipeOptions]::Asynchronous)
try {
 [IO.File]::WriteAllText($Ready,'ready')
 if (-not $pipe.WaitForConnectionAsync().Wait(15000)) { throw 'connect timeout' }
 $reader=[IO.StreamReader]::new($pipe,[Text.Encoding]::UTF8)
 for ($i=0;$i -lt 2;$i++) {
  $line=$reader.ReadLineAsync()
  if (-not $line.Wait(10000)) { throw 'read timeout' }
  Write-Output $line.Result
 }
} finally { $pipe.Dispose() }
''',encoding='utf-8')
            process=subprocess.Popen(['powershell','-NoProfile','-NonInteractive','-File',str(script),name,str(ready)],
                                     stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                deadline=time.monotonic()+10
                while not ready.exists() and process.poll() is None and time.monotonic()<deadline: time.sleep(.05)
                self.assertTrue(ready.exists(),'pipe fixture failed to start')
                env={'CLAUDE_CODE_SESSION_ID':SID,'CLAUDE_CODE_MESSAGING_SOCKET':'\\\\.\\pipe\\'+name,
                     'CLAUDE_CODE_MESSAGING_TOKEN':'synthetic-only'}
                with patch.dict(os.environ,env): submit(SID,MID,'hello from synthetic test')
                out,err=process.communicate(timeout=15)
                self.assertEqual(process.returncode,0,err.decode(errors='replace'))
                auth,event=[json.loads(line) for line in out.splitlines()]
                self.assertEqual(auth,{'type':'auth','token':'synthetic-only'})
                self.assertEqual(event['session_id'],SID)
                self.assertEqual(event['message']['content'],'hello from synthetic test')
            finally:
                if process.poll() is None: process.kill()
                process.communicate()


if __name__=='__main__': unittest.main()
