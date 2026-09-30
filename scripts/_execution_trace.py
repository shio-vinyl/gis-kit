"""Opt-in invocation evidence and conservative conversion to existing v1 recipes."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

from _delivery import digest
import _recipe_operations as adapters

SCRIPTS = Path(__file__).resolve().parent
SENSITIVE = re.compile(r'api[-_]?key|token|secret|password|authorization|cookie|credential|private[-_]?key', re.I)
REDACTED = '[REDACTED]'


def redact(value):
    """Best-effort credential filtering, not a general data-loss prevention system."""
    if isinstance(value, dict):
        return {k: REDACTED if SENSITIVE.search(k) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        if re.search(r'(?i)(?:https?://\S*[?@]|bearer\s+)', value) or re.search(
                r'(?i)(?:api[-_]?key|token|password|secret|cookie|authorization)\s*[=:]', value):
            return REDACTED
    return value


def redact_argv(argv):
    result = []
    hide_next = False
    for arg in argv:
        if hide_next:
            result.append(REDACTED)
            hide_next = False
        elif arg.startswith('-') and SENSITIVE.search(arg.split('=', 1)[0]):
            flag = arg.split('=', 1)[0]
            result.append(flag + '=' + REDACTED if '=' in arg else flag)
            hide_next = '=' not in arg
        else:
            result.append(redact(arg))
    return result


def atomic_json(path, value, *, replace=False):
    """Private per-invocation files avoid shared append locks and torn final records."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.trace-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)  # Atomic no-clobber publication.
    finally:
        temporary.unlink(missing_ok=True)


def map_parameter_paths(parameters, convert):
    result = copy.deepcopy(parameters)
    for name in adapters.PATH_PARAMS & result.keys():
        result[name] = convert(result[name])
    if 'profile' in result:
        if not isinstance(result['profile'], dict):
            raise ValueError('profile must be an object with an input path')
        result['profile']['input'] = convert(result['profile']['input'])
    return result


class CaptureParser(argparse.ArgumentParser):
    def error(self, message):
        # Do not print raw argument values to a trace or intercept the native CLI.
        raise ValueError('Invocation does not match a fixed recipe adapter')


def prepare(tool, argv):
    if tool != 'daily' and tool not in adapters.RUNNERS:
        raise ValueError('No fixed recipe adapter; invocation metadata only')
    parser = CaptureParser(add_help=False, allow_abbrev=False)
    if tool in ('daily', 'raster', 'terrain-backend'):
        parser.add_argument('operation')
    else:
        parser.set_defaults(operation='run')
    parser.add_argument('input', nargs='+' if tool == 'raster' else None)
    required, optional = (set(), {'right'}) if tool == 'daily' else adapters.RUNNERS[tool][1:]
    for flag in sorted((required | optional) - {'input', 'inputs'}):
        parser.add_argument('--' + flag, dest=flag, required=flag in required)
    if tool == 'spatial-inference':
        for flag in ('backend-site-packages', 'backend-proj-data'):
            parser.add_argument('--' + flag, dest=flag)
    parser.add_argument('--params', required=True)
    parser.add_argument('--output', required=True)
    parsed = vars(parser.parse_args(argv))
    if tool == 'daily' and parsed['operation'] not in adapters.DAILY_OPERATIONS:
        raise ValueError('Not a daily recipe operation')
    parameter_file = Path(parsed.pop('params')).resolve()
    if parameter_file.stat().st_size > 1024 * 1024:
        raise ValueError('Parameter snapshot exceeds 1 MiB; invocation metadata only')
    parameter_bytes = parameter_file.read_bytes()
    if len(parameter_bytes) > 1024 * 1024:
        raise ValueError('Parameter snapshot exceeds 1 MiB; invocation metadata only')
    parameters = json.loads(parameter_bytes)
    if not isinstance(parameters, dict):
        raise ValueError('Recipe parameters must be an object')
    json.dumps(parameters, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if redact(parameters) != parameters or redact_argv(argv) != argv:
        raise ValueError('Credential-bearing invocation is not recipe-promotable')
    output = Path(parsed.pop('output')).resolve()
    if output.exists():
        raise ValueError('Tracing cannot adopt a pre-existing output bundle')
    operation = parsed.pop('operation')
    backend = {k: str(Path(parsed.pop(k)).resolve()) for k in ('backend-site-packages', 'backend-proj-data')
               if parsed.get(k) is not None}
    parsed = {k: v for k, v in parsed.items() if v is not None}
    if tool == 'raster':
        parsed['inputs'] = parsed.pop('input')
    files = {k: [str(Path(v).resolve()) for v in value] if isinstance(value, list)
             else str(Path(value).resolve()) for k, value in parsed.items()}
    paths = [p for value in files.values() for p in (value if isinstance(value, list) else [value])]

    def parameter_path(value):
        if not isinstance(value, str):
            raise ValueError('CLI file parameters must be paths')
        path = str(Path(value).resolve())
        paths.append(path)
        return path

    parameters = map_parameter_paths(parameters, parameter_path)
    # Use the actual recipe fingerprint contract, including sidecar rejection.
    from recipe import fingerprint
    return {'runner': tool, 'operation': operation, 'files': files, 'params': parameters,
            'backend': backend, 'output': str(output), 'parameter_file': str(parameter_file),
            'parameter_sha256': hashlib.sha256(parameter_bytes).hexdigest(),
            'inputs': {p: fingerprint(p) for p in sorted(set(paths))},
            'implementation': {p.name: digest(p) for p in sorted(SCRIPTS.glob('*.py'))}}


def finish_capture(capture):
    from recipe import fingerprint
    if capture['parameter_sha256'] != digest(capture['parameter_file']):
        raise ValueError('Parameters changed during execution')
    if capture['inputs'] != {p: fingerprint(p) for p in capture['inputs']}:
        raise ValueError('Inputs changed during execution')
    if capture['implementation'] != {p.name: digest(p) for p in sorted(SCRIPTS.glob('*.py'))}:
        raise ValueError('Implementation changed during execution')
    output = Path(capture['output'])
    record = json.loads((output / 'record.json').read_text(encoding='utf-8'))
    capture['source_status_present'] = 'status' in record
    capture['source_status'] = record.get('status')
    capture['artifacts'] = adapters.inventory(output)
    artifact = record.get('artifact')
    capture['artifact_hint'] = artifact.get('name') if isinstance(artifact, dict) else artifact
    allowed = ('complete',) if capture['runner'] == 'daily' else ('complete', 'candidate')
    if 'status' in record and record['status'] not in allowed:
        raise ValueError('Held, unknown or unsupported result status; review outside recipe replay')


def run_traced(directory, tool, argv, command):
    directory = Path(directory).resolve()
    record = {'schema_version': 1, 'kind': 'gis-kit-execution', 'id': uuid.uuid4().hex,
              'tool': tool, 'argv': redact_argv(argv), 'cwd': str(Path.cwd()),
              'python': sys.executable, 'python_version': sys.version,
              'started_ns': time.time_ns(), 'execution_status': 'started',
              'returncode': None, 'recipe_eligible': False}
    capture = None
    try:
        capture = prepare(tool, argv)
    except (ValueError, OSError, KeyError, TypeError, ImportError):
        record['promotion_error'] = 'Unsupported invocation, unavailable inputs, redaction, or parameter snapshot limit; metadata only'
    if capture is not None:
        output = Path(capture['output'])
        if directory == output or output in directory.parents:
            raise ValueError('Trace directory must be outside the command output bundle')
        record['capture'] = capture
    path = directory / (record['id'] + '.json')
    atomic_json(path, record)
    print(f"Trace: {path}", file=sys.stderr)
    code = 1
    try:
        process = subprocess.run(command)
        code = process.returncode
        record['returncode'] = code
        record['execution_status'] = 'finished' if code == 0 else 'failed'
        if code == 0 and capture is not None:
            try:
                finish_capture(capture)
                record['recipe_eligible'] = True
            except (ValueError, OSError, KeyError, TypeError):
                record['promotion_error'] = 'Result not promotable: state, input/parameter/implementation drift, or missing bundle evidence'
    except KeyboardInterrupt:
        code = 130
        record['returncode'] = code
        record['execution_status'] = 'interrupted'
    except OSError:
        record['execution_status'] = 'launch_failed'
        raise
    finally:
        record['finished_ns'] = time.time_ns()
        atomic_json(path, record, replace=True)
    return code if code >= 0 else 128 - code


def distill(directory, output, selected=None, artifacts=None):
    """Select a chronological sequence; preserve explicit files, never infer a DAG."""
    records = []
    for path in sorted(Path(directory).glob('*.json')):
        if path.name.startswith('.'):
            continue
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('schema_version') != 1 or data.get('kind') != 'gis-kit-execution':
            raise ValueError(f'Not an execution record: {path.name}')
        records.append(data)
    ids = [r['id'] for r in records]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate trace identities')
    if selected is not None:
        if len(selected) != len(set(selected)) or not set(selected) <= set(ids):
            raise ValueError('Unknown or duplicate selected trace identity')
        records = [r for r in records if r['id'] in selected]
    if not records:
        raise ValueError('No execution records selected')
    records.sort(key=lambda r: (r['started_ns'], r['id']))
    artifacts = artifacts or {}
    if not set(artifacts) <= {r['id'] for r in records}:
        raise ValueError('Artifact override must name a selected trace identity')
    from recipe import fingerprint
    for index, record in enumerate(records):
        if not record.get('recipe_eligible') or record.get('returncode') != 0 or record.get('execution_status') != 'finished':
            raise ValueError(f"Trace {record['id']} is not recipe-eligible; select an explicit successful subset")
        if record['finished_ns'] < record['started_ns'] or (index and records[index-1]['finished_ns'] > record['started_ns']):
            raise ValueError('Overlapping or invalid execution intervals; no sequential order can be inferred')
        cap = record['capture']
        if cap['inputs'] != {p: fingerprint(p) for p in cap['inputs']}:
            raise ValueError('Recorded inputs have changed or disappeared')
        if cap['artifacts'] != adapters.inventory(Path(cap['output'])):
            raise ValueError('Recorded result bundle has changed')
    recipe = {'schema_version': 1, 'inputs': {}, 'steps': []}
    refs = {}

    def input_ref(path, hashes):
        identity = (path, hashes[path])
        if identity not in refs:
            role = f'input_{len(recipe["inputs"]) + 1:03d}'
            refs[identity] = role
            recipe['inputs'][role] = path
        return {'input_ref': refs[identity]}

    for index, record in enumerate(records):
        cap = record['capture']
        output_dir = Path(cap['output'])
        consumed = {str(Path(p).relative_to(output_dir))
                    for later in records[index+1:] for p in later['capture']['inputs']
                    if output_dir in Path(p).parents}
        if any(Path(name).parent != Path('.') for name in consumed):
            raise ValueError('A trace step feeds a nested artifact; existing recipes expose only top-level artifacts')
        if len(consumed) > 1:
            raise ValueError('A trace step feeds multiple artifacts; existing recipes select one artifact per step')
        artifact = artifacts.get(record['id']) or next(iter(consumed), None) or cap['artifact_hint']
        if artifact is None:
            candidates = [p for p in cap['artifacts'] if p != 'record.json' and Path(p).parent == Path('.')
                          and Path(p).suffix in ('.tif', '.gpkg', '.csv', '.json')]
            if len(candidates) == 1:
                artifact = candidates[0]
        if not isinstance(artifact, str) or Path(artifact).name != artifact or artifact not in cap['artifacts']:
            raise ValueError(f"Trace {record['id']} needs an explicit --artifact ID=FILENAME")
        if consumed and consumed != {artifact}:
            raise ValueError('Artifact override disagrees with a later recorded input')
        if cap['runner'] == 'daily' and artifact != cap['artifact_hint']:
            raise ValueError('Daily recipes expose only their native result artifact')
        step = {'id': f'step_{index+1:03d}', 'operation': cap['operation'],
                'params': map_parameter_paths(cap['params'], lambda p: input_ref(p, cap['inputs']))}
        if cap['runner'] == 'daily':
            for name, value in cap['files'].items():
                step[name] = input_ref(value, cap['inputs'])['input_ref']
        else:
            step.update(runner=cap['runner'], artifact=artifact,
                        files={k: [input_ref(p, cap['inputs']) for p in value] if isinstance(value, list)
                               else input_ref(value, cap['inputs']) for k, value in cap['files'].items()})
            if cap['backend']:
                step['backend'] = cap['backend']
            if cap['source_status'] == 'candidate':
                step['validate'] = {'allow_candidate': False}  # Agent must review; never auto-approve.
        recipe['steps'].append(step)
        refs[(str(output_dir / artifact), cap['artifacts'][artifact])] = step['id']
    atomic_json(output, recipe)
    return {'output': str(Path(output).resolve()), 'steps': len(records),
            'selected': [r['id'] for r in records], 'omitted': sorted(set(ids) - {r['id'] for r in records}),
            'review_required': True, 'executed': False}


def distill_main(argv=None):
    parser = argparse.ArgumentParser(description='Create a v1 recipe draft from recorded native calls; never execute or grant semantic acceptance')
    parser.add_argument('trace', help='Directory created by gis.py --trace')
    parser.add_argument('--output', required=True, help='New recipe JSON file; no overwrite')
    parser.add_argument('--select', nargs='+', help='Explicit record IDs; default requires every record to be eligible')
    parser.add_argument('--artifact', action='append', default=[], metavar='ID=FILENAME', help='Select a terminal multi-artifact result')
    args = parser.parse_args(argv)
    try:
        overrides = {}
        for value in args.artifact:
            key, filename = value.split('=', 1)
            if key in overrides:
                raise ValueError('Duplicate artifact override')
            overrides[key] = filename
        print(json.dumps(distill(args.trace, args.output, args.select, overrides), indent=2))
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, f'ERROR: {error}\n')
