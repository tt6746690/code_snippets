import re
import os
import subprocess
import shlex
import tempfile
from datetime import datetime, timedelta
import uuid



shell_scripts_template = """
echo "Running on $SLURM_JOB_NODELIST"
echo "======"

MASTER_ADDR=$(scontrol show hostnames "$SLURM_JOB_NODELIST" | head -n 1)
MASTER_PORT=10002
RDZV_ENDPOINT=$MASTER_ADDR:$MASTER_PORT

source {profile}
conda activate {conda_env}
cd {cwd}

set -e
set -x
echo "======"

srun {cmd}

[ ! -f "{log_dir}/$SLURM_JOB_ID*.out" ] && mv {log_dir}/$SLURM_JOB_ID*.out {save_dir} ||:
"""


def multiline_to_singleline(cmd):
    cmd = cmd.strip()
    cmd = re.sub(r'\\(?![$])', '', cmd) # replace all '\' but not '\$'
    cmd = cmd.replace('\n', '')
    cmd = re.sub(' +', ' ', cmd)
    cmd = cmd.strip()
    return cmd


def hours_to_slurm_time(hours):
    td = timedelta(hours=hours)
    days = td.days
    total_seconds = td.seconds
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    # Format as 'DD-HH:MM:SS'
    base_time = datetime(1, 1, 1, hours, minutes, seconds)
    s = f"{days:02d}-{base_time.strftime('%H:%M:%S')}"
    return s


def submit_job_slurm(
    shell_scripts: str,
    job_name='wpq-job',
    partition='learnai',
    nodes=1,
    num_cpus=1,
    cpu_mem=3,
    num_gpus=0,
    time=24,
    log_path=None,
    test_run=False,
    num_jobs=1,
    exclude=None,
    sbatch_dir="/fsx/wpq/.sbatch",
    shell_scripts_modification_fn=None,
):
    """
        submit to SLURM scheduler (via `sbatch`) a job that executes `shell_scripts`,
            with specified resources.

        Usage
        ```
        from rosemary.submit import submit_job_slurm

        # submit simple bash commands, with minimal resources
        submit_job_slurm('echo hello world', partition='learnai')

        # test job chaining
        out = submit_job_slurm('echo foo; echo bar', partition='learnai', test_run=False, num_jobs=2, job_name='test.out', shell_scripts_modification_fn=lambda x: x.replace('bar', 'baz'))
        ```
    """
    if isinstance(shell_scripts, list):
        return [
            submit_job_slurm(
                shell_scripts=shell_scripts,
                job_name=job_name,
                partition=partition,
                nodes=nodes,
                num_cpus=num_cpus,
                cpu_mem=cpu_mem,
                num_gpus=num_gpus,
                time=time,
                log_path=log_path,
                test_run=test_run,
                num_jobs=num_jobs,
                exclude=exclude,
                sbatch_dir=sbatch_dir,
                shell_scripts_modification_fn=shell_scripts_modification_fn) 
            for x in shell_scripts]

    if log_path is None:
        log_path = os.path.join(os.getcwd(), '%J.out')
    if '.out' not in log_path:
        raise ValueError('log_path must contain ".out"')

    os.makedirs(sbatch_dir, exist_ok=True)
    
    # unset `SLURM_MEM_PER_CPU` since `--men` sets `SLURM_MEM_PER_NODE`.
    # srun: fatal: SLURM_MEM_PER_CPU, SLURM_MEM_PER_GPU, and SLURM_MEM_PER_NODE are mutually exclusive.
    for k in ['SLURM_MEM_PER_CPU']:
        if k in os.environ: os.environ.pop(k)
    
    log_dir = os.path.dirname(log_path)
    log_filename = os.path.basename(log_path)
    log_filename_noext, ext = log_filename.split('.')

    info = []
    job_id = '<job_id>'
    for i in range(num_jobs):
        # for >1 jobs, the `log_filename` is modified with `_i:num_jobs` before extension.
        if num_jobs > 1:
            log_path = os.path.join(
                log_dir, '.'.join([log_filename_noext+f'_{i+1}:{num_jobs}', ext]))

        if shell_scripts_modification_fn is not None:
            shell_scripts_cmd = shell_scripts_modification_fn(shell_scripts) \
                if i != 0 else shell_scripts
        else:
            shell_scripts_cmd = shell_scripts

        sbatch_args = []
        sbatch_args += [
            ('job-name', job_name),
            ('partition', partition),
            ('nodes', nodes),
            ('cpus-per-task', num_cpus),
            ('mem', f'{cpu_mem}gb'),
        ]
        if num_gpus > 0:
            sbatch_args += [
                ('gres', f'gpu:{num_gpus}'),
            ]
        sbatch_args += [
            ('time', hours_to_slurm_time(time)),
            ('output', log_path),
        ]
        if exclude is not None:
            sbatch_args += [
                ('exclude', exclude),
            ]
        if i != 0:
            sbatch_args += [
                ('dependency', f'afterok:{str(job_id)}'),
            ]
        
        s = "" 
        s += "#!/bin/bash\n\n"
        for k, v in sbatch_args:
            if v is not None:
                s += f'#SBATCH --{k}={v}\n'
        s += '\n'
        s += shell_scripts_cmd
        
        timestamp = datetime.now().strftime("%Y-%m-%d_%H:%M:%S")
        random_uuid = uuid.uuid4()
        sbatch_script_filepath = os.path.join(
            sbatch_dir,
            f"{timestamp}_{random_uuid}.sh"
        )
        with open(sbatch_script_filepath, 'w') as f:
            f.write(s)

        sbatch_cmd = f"sbatch {sbatch_script_filepath}"
        sbatch_cmd = shlex.split(sbatch_cmd)

        job_info = {
            'args': ' '.join(sbatch_cmd),
        }
        if test_run is False:
            try:
                p = subprocess.Popen(sbatch_cmd,
                                     stdout=subprocess.PIPE, 
                                     stderr=subprocess.PIPE)
                stdout, stderr = p.communicate()
                stdout = stdout.decode("utf-8")
                match = re.search(r"Submitted batch job (\d+)", stdout)
                job_id = int(match.group(1)) if match else stdout
            except Exception as e:
                print(e)
                pass
            job_info.update({
                'job_id': job_id,
            })
        info.append(job_info)

    return info


