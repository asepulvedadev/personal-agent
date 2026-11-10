"""Restore only this project's edge attachment if the shared edge is recreated."""
import json
import subprocess
import time
while True:
    try:
        p=subprocess.run(['docker','inspect','youking-edge-1'],capture_output=True,text=True)
        if p.returncode==0:
            networks=json.loads(p.stdout)[0]['NetworkSettings']['Networks']
            if 'personal-agent_front' not in networks:
                r=subprocess.run(['docker','network','connect','--ip','172.30.42.3','--gw-priority','-1','personal-agent_front','youking-edge-1'],capture_output=True,text=True)
                print('Workspace frontend attachment restored' if r.returncode==0 else 'Workspace frontend attachment pending',flush=True)
    except Exception as e:
        print('Edge link check:',type(e).__name__,flush=True)
    time.sleep(20)
