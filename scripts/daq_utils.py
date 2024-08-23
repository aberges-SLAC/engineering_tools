####################################################
# Utilities to run the daq and view its status.
#
# Do not use non-standard python.
####################################################
import subprocess
from subprocess import PIPE
import socket
import os, sys
import getpass


DAQBATCH_HUTCHES=['tmo', 'rix']
LOCALHOST = socket.gethostname()
SLURM_PARTITION='drpq'
SLURM_JOBNAME='submit_daq'
DAQBATCH_SCRIPT='submit_daq.sh'
DAQBATCH_OUTPUT='slurm_daq.log'

def silentremove(filename):
    try:
        os.remove(filename)
    except OSError as e:
        if e.errno != errno.ENOENT:  # errno.ENOENT = no such file or directory
            raise

def call_subprocess(*args):
    cc = subprocess.run(args, stdout=PIPE, stderr=PIPE)
    output = None
    if not cc.returncode:
        output = str(cc.stdout.strip(), "utf-8")
    return output

def call_sbatch(cmd, nodelist, scripts_dir):
    sb_script = "#!/bin/bash\n"
    sb_script += f"#SBATCH --partition={SLURM_PARTITION}" + "\n"
    sb_script += f"#SBATCH --job-name={SLURM_JOBNAME}" + "\n"
    sb_script += f"#SBATCH --nodelist={nodelist}" + "\n"
    daqbatch_output = os.path.join(scripts_dir, DAQBATCH_OUTPUT)
    sb_script += f"#SBATCH --output={daqbatch_output}" + "\n"
    sb_script += f"#SBATCH --ntasks=1" + "\n"
    sb_script += f"unset PYTHONPATH" + "\n"
    sb_script += f"unset LD_LIBRARY_PATH" + "\n"
    sb_script += cmd
    daqbatch_script = os.path.join(scripts_dir, DAQBATCH_SCRIPT)
    with open(daqbatch_script, "w") as f:
        f.write(sb_script)
    #call_subprocess('sbatch', daqbatch_script)
    #silentremove(daqbatch_script)

class SbatchManager:
    def __init__(self, user):
        self.user = user


    def get_job_info(self):
        # Use squeue to get JobId, Comment, JobName, Status, and Reasons (Node List)
        format_string = '"%i %k %j %T %R"'
        lines = call_subprocess(
                    "squeue", "-u", self.user, "-h", "-o", format_string
                ).splitlines()
        job_details = {}
        for i, job_info in enumerate(lines):
            cols = job_info.strip('"').split()
            success = True
            if len(cols) == 5:
                job_id, comment, job_name, state, nodelist = cols
            elif len(cols) > 5:
                job_id, comment, job_name, state = cols[:4]
                nodelist = " ".join(cols[5:])
            else:
                success = False
            if success:
                # Get logfile from job_id
                scontrol_lines = call_subprocess(
                    "scontrol", "show", "job", job_id
                ).splitlines()
                logfile = ""
                for scontrol_line in scontrol_lines:
                    if scontrol_line.find("StdOut") > -1:
                        scontrol_cols = scontrol_line.split("=")
                        logfile = scontrol_cols[1]

                job_details[comment] = {
                    "job_id": job_id,
                    "job_name": job_name,
                    "state": state,
                    "nodelist": nodelist,
                    "logfile": logfile,
                }
        return job_details


class ProcMgrHelper:
    def __init__(self, platform, cnf_file):
        self.platform = platform
        self.cnf_file = cnf_file
        self.parse_cnf()

    def parse_cnf(self):
        self.config = {'platform': repr(self.platform), 'procmgr_config': None, 'TESTRELDIR': None,
                  'CONFIGDIR': os.path.dirname(os.path.abspath(self.cnf_file)),
                  'id': 'id', 'cmd': 'cmd', 'flags': 'flags', 'port': 'port', 'host': 'host',
                  '__file__': self.cnf_file,
                  'rtprio': 'rtprio', 'env': 'env', 'evr': 'evr', 'conda': 'conda', 'procmgr_macro': {}}
        try:
            exec(compile(open(self.cnf_file).read(), self.cnf_file, 'exec'), {}, self.config)
        except:
            pass
            #print('Error parsing configuration file:', sys.exc_info()[1]) 

    def get_value(self, parm):
        if parm in self.config:
            return self.config[parm]


class DaqManager:
    def __init__(self, opts):
        self.opts = opts
        self.hutch = call_subprocess("get_info", "--gethutch")
        self.user = self.hutch+'opr'
        self.sbman = SbatchManager(self.user)
        self.scripts_dir = f'/reg/g/pcds/dist/pds/{self.hutch}/scripts'
        self.set_cnf_file_and_platform()

    def isdaqbatch(self):
        if self.hutch in DAQBATCH_HUTCHES:
            return True
        else:
            return False

    def isvaliduser(self):
        if getpass.getuser().find('opr') > -1:
            return True
        return False

    def set_cnf_file_and_platform(self):
        self.cnf_file = None
        for o, a in self.opts:
            if o in ('-d', '--dss'):
                self.cnf_file = os.path.join(self.scripts_dir, 'dss.cnf')
        if self.cnf_file is None:
            if LOCALHOST == 'cxi-daq':
                cnf_ext = '_0.cnf'
            elif LOCALHOST == 'cxi-monitor':
                cnf_ext = '_1.cnf'
            elif self.isdaqbatch():
                cnf_ext = '.py'
            else:
                cnf_ext = '.cnf'
            self.cnf_file = f'{self.hutch}{cnf_ext}'
        
        # TODO: Read platform from the configuration file
        # Current block is for daqbatch, we use psana2 slurm.Config class.
        # This is current not working in the environment without psana2.
        self.platform = 0
        if LOCALHOST == 'cxi-monitor':
            self.platform = 1
        elif self.hutch == 'rix':
            self.platform = 2

    def wheredaq(self, quiet=False):
        """ Locate where the daq is running.
        
        For procmgr, we check the running cnf for active daq.
        For daqbatch, we use slurm to check if the hutch user is running control_gui.
        """
        daq_host = None
        if self.isdaqbatch():
            # Use control_gui job name to locate the running host for the daq
            job_details = self.sbman.get_job_info()
            if 'control_gui' in job_details:
                daq_host = job_details['nodelist']
        else:
            # For other hutches, we get daq host from the running cnf file. 
            cnf_file = os.path.join(self.scripts_dir, f'p{self.platform}.cnf.running')
            if os.path.exists(cnf_file):
                procmgr_helper = ProcMgrHelper(self.platform, cnf_file)
                daq_host = procmgr_helper.get_value('procmgr_macro')['HOST']
        
        if quiet: return daq_host
        if daq_host is None:
            if LOCALHOST == 'cxi-daq':
                print(f'Main DAQ cxi_0 is not running on {LOCALHOST}')
            elif LOCALHOST == 'cxi-monitor':
                print(f'Secondary DAQ cxi_1 is not running on {LOCALHOST}')
            print(f'DAQ is not running in {self.hutch}')
        else:
            print(f'DAQ is running on {daq_host}')
    
    def calldaq(self, subcmd, daq_host=None):
        if self.isdaqbatch(): 
            prog = 'daqbatch'
        else:
            prog = 'procmgr'
        cmd = f'pushd {self.scripts_dir}'+'\n'
        cmd += f'source {os.path.join(self.scripts_dir, "setup_env.sh")}'+'\n'
        cmd += f'{prog} {subcmd} {self.cnf_file}'+'\n'
        cmd += f'popd'+'\n'
        if daq_host is None:
            daq_host = self.wheredaq(quiet=True)
            if daq_host is None:
                daq_host = LOCALHOST
        call_sbatch(cmd, daq_host, self.scripts_dir)

        if subcmd == 'stop':
            if self.wheredaq(quiet=True) is not None:
                print('the DAQ did not stop properly, exit now and try again or call your POC or the DAQ phone')

    def stopdaq(self):
        """ Stop the running daq for the current user.
        Note that daq host is determined by .running file or squeue (daqbatch).
        """
        self.calldaq('stop')

    def startdaq(self, daq_host):
        self.calldaq('start', daq_host=daq_host)

    def restartdaq(self):
        if not self.isvaliduser():
            print(f'Please run the DAQ from the operator account!')
            return
        self.stopdaq()
        
        daq_host = LOCALHOST
        # User can use -m to specify the host to run the daq
        for o, a in self.opts:
            if o == '-m':
                daq_host = a
                break
        self.startdaq(daq_host)








        
