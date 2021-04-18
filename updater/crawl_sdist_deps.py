import json
import os
import subprocess as sp
import traceback
import itertools
from dataclasses import asdict, dataclass, field
from random import shuffle
from tempfile import TemporaryDirectory
from time import sleep, time
from typing import Union, List, ContextManager

import utils
from bucket_dict import LazyBucketDict


@dataclass
class PackageJob:
    bucket: str
    name: str
    version: str
    url: Union[None, str]
    sha256: Union[None, str]
    idx: int
    timeout: int = field(default=60)


@dataclass
class JobResult:
    name: str
    version: str
    py_ver: str
    error: Union[None, str] = None
    install_requires: Union[None, str, list, dict] = field(default_factory=list)
    setup_requires: Union[None, str, list, dict] = field(default_factory=list)
    extras_require: Union[None, str, list, dict] = field(default_factory=list)
    tests_require: Union[None, str, list, dict] = field(default_factory=list)
    python_requires: Union[None, str, list, dict] = field(default_factory=list)


@dataclass
class PKG:
    install_requires: str
    setup_requires: str
    extras_require: str
    tests_require: str
    python_requires: str


def extractor_cmd(pkg_name, pkg_ver, out='./result', url=None, sha256=None, substitutes=True, store=None) -> List[str]:
    extractor_dir = os.environ.get("EXTRACTOR_DIR")
    if not extractor_dir:
        raise Exception("Set env variable 'EXTRACTOR_DIR'")
    base_args = [
        "--arg", "pkg", f'"{pkg_name}"',
        "--arg", "version", f'"{pkg_ver}"',
        "-o", out
    ]
    if store:
        base_args += ["--store", f"{store}"]
    if url and sha256:
        cmd = [
            "nix-build", f"{extractor_dir}/fast-extractor.nix",
            "--arg", "url", f'"{url}"',
            "--arg", "sha256", f'"{sha256}"'
        ] + base_args
    else:
        cmd = [
            "nix-build", f"{extractor_dir}/extractor.nix",
        ] + base_args
        print('using slow builder')
    if not substitutes:
        cmd += ["--option", "build-use-substitutes", "false"]
    return cmd


def format_log(log: str):
    """
    Postgres doesn't support indexing large text files.
    Therefore we limit line length and count
    """
    lines = log.splitlines(keepends=True)
    lines = map(lambda line: f"{line[:400]}\n" if len(line) > 400 else line, lines)
    remove_lines_marker = (
        '/homeless-shelter/.cache/pip/http',
        '/homeless-shelter/.cache/pip',
        'DEPRECATION: Python 2.7'
    )
    filtered = filter(lambda l: not any(marker in l for marker in remove_lines_marker), lines)
    return ''.join(list(filtered)[:90])


def extract_requirements(job: PackageJob, py_versions):
    # py_versions = ('python27', 'python36', 'python37', 'python38', 'python39', 'python310')
    py_versions = ('python27', 'python36', 'python37', 'python38')
    try:
        print(f"Bucket {job.bucket} - Job {job.idx} - {job.name}:{job.version}")
        store = os.environ.get('STORE', None)
        with TemporaryDirectory() as tempdir:
            out_dir = f"{tempdir}/json"
            cmd = extractor_cmd(job.name, job.version, out_dir, job.url, job.sha256,
                                store=store)
            # print(' '.join(cmd).replace(' "', ' \'"').replace('" ', '"\' '))
            try:
                sp.run(cmd, capture_output=True, timeout=job.timeout, check=True)
            except (sp.CalledProcessError, sp.TimeoutExpired) as e:
                print(f"problem with {job.name}:{job.version}")
                print(e.stderr.decode())
                formatted = format_log(e.stderr.decode())
                return [JobResult(
                    name=job.name,
                    version=job.version,
                    py_ver=f"{py_ver}",
                    error=formatted,
                ) for py_ver in py_versions]
            results = []
            for py_ver in py_versions:
                data = None
                try:
                    path = os.readlink(f"{out_dir}")
                    if store:
                        path = path.replace('/nix/store', f"{store}/nix/store")
                    with open(f"{path}/{py_ver}.json") as f:
                        content = f.read().strip()
                        if content != '':
                            data = json.loads(content)
                except FileNotFoundError:
                    pass
                if data is None:
                    with open(f"{path}/{py_ver}.log") as f:
                        error = format_log(f.read())
                    print(error)
                    results.append(JobResult(
                        name=job.name,
                        version=job.version,
                        py_ver=f"{py_ver}",
                        error=error,
                    ))
                else:
                    for k in ('name', 'version'):
                        if k in data:
                            del data[k]
                    results.append(JobResult(
                        name=job.name,
                        version=job.version,
                        py_ver=py_ver,
                        **data
                    ))
            return results
    except Exception as e:
        traceback.print_exc()
        return e


def get_jobs(pypi_index, bucket, processed, amount=1000):
    jobs = []
    names = list(pypi_index.by_bucket(bucket).keys())
    total_nr = 0
    for pkg_name in names:
        for ver, release_types in pypi_index[pkg_name].items():
            if 'sdist' not in release_types:
                continue
            if (pkg_name, ver) in processed:
                continue
            total_nr += 1
            release = release_types['sdist']
            if len(jobs) < amount:
                jobs.append(PackageJob(
                    bucket,
                    pkg_name,
                    ver,
                    f"https://files.pythonhosted.org/packages/source/{pkg_name[0]}/{pkg_name}/{release[1]}",
                    release[0],
                    0,
                ))
    shuffle(jobs)
    for i, job in enumerate(jobs):
        job.idx = i
    print(f"Bucket {bucket}: Planning execution of {len(jobs)} jobs out of {total_nr} total jobs for this bucket")
    return jobs


def get_processed():
    with open('/tmp/jobs', 'r') as f:
        return {tuple(t) for t in json.load(f)}


def build_base(store=None):
    # make sure base stuff gets back into cache after cleanup:
    cmd = extractor_cmd("requests", "2.22.0", out='/tmp/dummy', url='https://files.pythonhosted.org/packages/01/62/ddcf76d1d19885e8579acb1b1df26a852b03472c0e46d2b959a714c90608/requests-2.22.0.tar.gz',
                        sha256='11e007a8a2aa0323f5a921e9e6a2d7e4e67d9877e85773fba9ba6419025cbeb4', store=store)
    sp.check_call(cmd, timeout=1000)


def pkg_to_dict(pkg):
    pkg_dict = asdict(PKG(
        install_requires=pkg.install_requires,
        setup_requires=pkg.setup_requires,
        extras_require=pkg.extras_require,
        tests_require=pkg.tests_require,
        python_requires=pkg.python_requires
    ))
    new_release = {}
    for key, val in pkg_dict.items():
        if not val:
            continue
        if key == 'extras_require':
            for extra_key, extra_reqs in val.items():
                val[extra_key] = list(flatten_req_list(extra_reqs))
        if key not in flatten_keys:
            new_release[key] = val
            continue
        val = list(flatten_req_list(val))
        if isinstance(val, str):
            val = [val]
        if not all(isinstance(elem, str) for elem in val):
            print(val)
            raise Exception('Requirements must be list of strings')
        new_release[key] = val
    return new_release


def flatten_req_list(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, list):
        if len(obj) == 0:
            return
        elif len(obj) == 1:
            for s in flatten_req_list(obj[0]):
                yield s
        else:
            for elem in obj:
                for s in flatten_req_list(elem):
                    yield s
    else:
        raise Exception('Is not list or str')


flatten_keys = (
    'setup_requires',
    'install_requires',
    'tests_require',
    'python_requires',
)


def insert(py_ver, name, ver, release, target, error=False):
    if error:
        data = "err"
    else:
        data = release
    ver = ver.strip()
    # create structure
    if name not in target:
        target[name] = {}
    if ver not in target[name]:
        target[name][ver] = {}
    target[name][ver][py_ver] = data


def compress_dict(d, sort=True):
    if sort:
        items = sorted(d.items(), key=lambda x: x[0])
    else:
        items = d.items()
    keep = {}
    for k, v in items:
        for keep_key, keep_val in keep.items():
            if v == keep_val:
                d[k] = keep_key
                break
        if not isinstance(d[k], str):
            keep[k] = v


def decompress_dict(d):
    keys = set(d.keys())
    for k, v in d.items():
        if isinstance(v, str) and v in keys:
            d[k] = d[v]


def compress(pkgs_dict: LazyBucketDict):
    for name, vers in pkgs_dict.items():
        for ver, pyvers in vers.items():
            compress_dict(pyvers)
        compress_dict(vers)


def decompress(pkgs_dict: LazyBucketDict):
    for name, vers in pkgs_dict.items():
        decompress_dict(vers)
        for ver, pyvers in vers.items():
            decompress_dict(pyvers)


def purge(pypi_index, pkgs_dict: LazyBucketDict, bucket, py_vers):
    # purge all versions which are not on pypi anymore
    for name, vers in pkgs_dict.by_bucket(bucket).copy().items():
        if name not in pypi_index:
            del pkgs_dict[name]
            continue
        for ver in tuple(vers.keys()):
            if ver not in pypi_index[name]:
                del pkgs_dict[name][ver]
    # purge old python versions
    for name, vers in pkgs_dict.by_bucket(bucket).copy().items():
        for ver, pyvers in vers.copy().items():
            for pyver in tuple(pyvers.keys()):
                if pyver not in py_vers:
                    del pkgs_dict[name][ver][pyver]
            if len(pkgs_dict[name][ver]) == 0:
                del pkgs_dict[name][ver]
        if len(pkgs_dict[name]) == 0:
            del pkgs_dict[name]


class Measure(ContextManager):
    def __init__(self, name):
        self.name = name
    def __enter__(self):
        self.enter_time = time()
        print(f'beginning "{self.name}"')
    def __exit__(self, exc_type, exc_val, exc_tb):
        dur = round(time() - self.enter_time, 1)
        print(f'"{self.name}" took {dur}s')


def main():
    workers = int(os.environ.get('WORKERS', "1"))
    num_jobs = int(os.environ.get('JOBS', "1000"))
    dump_dir = os.environ.get('DUMP_DIR', "./sdist")
    py_vers_short = os.environ.get('PYTHON_VERSIONS', "27,36,37,38,39,310").strip().split(',')
    py_vers_nix = tuple(map(lambda v: f"python{v}", py_vers_short))
    pypi_fetcher_dir = os.environ.get('PYPI_FETCHER', '/tmp/pypi_fetcher')
    build_base(store=os.environ.get('STORE', None))

    for bucket in list(LazyBucketDict.bucket_keys()):
        pkgs_dict = LazyBucketDict(dump_dir, restrict_to_bucket=bucket)
        pypi_index = LazyBucketDict(f"{pypi_fetcher_dir}/pypi", restrict_to_bucket=bucket)
        with Measure('Get processed pkgs'):
            # processed = set((p.name, p.version) for p in P.select(P.name, P.version).distinct())
            processed = set(
                itertools.chain.from_iterable(map(lambda t: ((t[0], vk) for vk in t[1].keys()), pkgs_dict.items())))
            print(f"DB contains {len(processed)} pkgs at this time for bucket {bucket}")
        with Measure("decompressing data"):
            decompress(pkgs_dict.by_bucket(bucket))
        # purge data for old python versions and packages which got deleted from pypi
        with Measure("purging packages"):
            purge(pypi_index, pkgs_dict, bucket, py_vers_short)
        with Measure("getting jobs"):
            jobs = get_jobs(pypi_index, bucket, processed, amount=num_jobs)
            if not jobs:
                continue
        with Measure('batch'):
            if workers > 1:
                pool_results = utils.parallel(
                    extract_requirements,
                    (jobs, list(py_vers_nix) * len(jobs)),
                    workers=workers,
                    use_processes=False)
            else:
                pool_results = [extract_requirements(args, py_vers_nix) for args in jobs]
        results = []

        # filter out exceptions and print them
        for i, res in enumerate(pool_results):
            if isinstance(res, Exception):
                print(f"Problem with {jobs[i].name}:{jobs[i].version}")
                if isinstance(res, sp.CalledProcessError):
                    print(res.stderr)
                traceback.print_exception(res, res, res.__traceback__)
            else:
                for r in res:
                    results.append(r)

        # insert new data
        for pkg in sorted(results, key=lambda pkg: (pkg.name, pkg.version, pkg.py_ver)):
            py_ver = ''.join(filter(lambda c: c.isdigit(), pkg.py_ver))
            insert(py_ver, pkg.name, pkg.version, pkg_to_dict(pkg), pkgs_dict, error=bool(pkg.error))

        # compress and save
        with Measure("compressing data"):
            compress(pkgs_dict.by_bucket(bucket))
        print("finished compressing data")
        with Measure("saving data"):
            pkgs_dict.save()


if __name__ == "__main__":
    main()
