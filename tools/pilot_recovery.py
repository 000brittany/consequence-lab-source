"""One reviewed protocol-failure replacement; no simulator or agent policy."""
import hashlib
import io
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
import zipfile

RUN = 42317362851
ARTIFACT = 13550454851
ORIGINAL_HEAD = 'c4f7f37a29f4b61ad541ff6863beb1d2907e40f4'
ORIGINAL_SOURCE = 'cf31a085e70c8bcd2b1ab6c04200b69e67dae077'
SOURCE = 'faeccaa03b8e4a0f4129d916ff520f4d5a4c0111'
VERSION = 'rooms-v2-pressure-communication-v1'
MANIFEST_SHA256 = '83fcc91176255d119e3ecfa6f500188072599c49eb2f237f6082457a07464e7f'
ZIP_SHA256 = '91024898489df1ffb68f89fd54637d0c593216ca5f4a96c8e35b998168716ea7'
AUTHORIZATION = 'AUTHORIZE_PROTOCOL_REPAIR_RECOVERY_42317362851_ONCE_192_CALLS_USD_10'
SUFFIX = ' | recovery=protocol-42317362851-once'
ORIGINAL_TITLE = f'Rooms v2 pilot | paid | scenario=diagnostic | {VERSION} | core=cf31a08'
LIMITS = {'calls':192,'spend_nano_usd':10_000_000_000,'elapsed_seconds':7200,
          'total_tokens':20_000_000,'input_tokens':65536,'output_tokens':8192,'min_interval_seconds':1}


def enabled(env):
    value = env.get('PROTOCOL_RECOVERY', 'false')
    if value not in ('true', 'false'):
        raise ValueError('Invalid protocol-recovery selector')
    return value == 'true'


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def configuration(source, version, manifest=None, limits=None):
    if (source,version) != (SOURCE,VERSION):
        raise ValueError('Recovery requires the exact repaired source/version')
    if manifest is not None and digest(manifest) != MANIFEST_SHA256:
        raise ValueError('Recovery requires the exact original experimental manifest')
    if limits is not None and limits != LIMITS:
        raise ValueError('Recovery limits differ from the reviewed configuration')


def original_run(run):
    expected = {'id':RUN,'head_sha':ORIGINAL_HEAD,'display_title':ORIGINAL_TITLE,
        'event':'workflow_dispatch','status':'completed','conclusion':'failure','run_attempt':1,
        'path':'.github/workflows/longitudinal-pilot.yml'}
    if any(run.get(k) != v for k,v in expected.items()):
        raise ValueError('Original protocol-failure run identity/status changed')


def original_artifact(artifact):
    expected = {'id':ARTIFACT,'name':f'longitudinal-pilot-diagnostic-paid-{RUN}-1',
        'expired':False,'digest':'sha256:'+ZIP_SHA256}
    origin = artifact.get('workflow_run',{})
    if (any(artifact.get(k) != v for k,v in expected.items())
            or origin.get('id') != RUN or origin.get('head_sha') != ORIGINAL_HEAD):
        raise ValueError('Original research-artifact identity/digest changed or unavailable')


def inspect_zip(payload):
    if hashlib.sha256(payload).hexdigest() != ZIP_SHA256:
        raise ValueError('Original research ZIP digest mismatch')
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        # Read only required small JSON records. Do not extract or execute files.
        names = ('manifest.json','source.json','launch.json','research/calibration.json')
        if any(archive.namelist().count(n) != 1 or archive.getinfo(n).file_size > 1_000_000 for n in names):
            raise ValueError('Missing, duplicate or oversized original provenance')
        manifest,source,launch,report = (json.loads(archive.read(n)) for n in names)
    if (digest(manifest) != MANIFEST_SHA256 or source.get('manifest_sha256') != MANIFEST_SHA256
            or source.get('core') != ORIGINAL_SOURCE or source.get('pilot_version') != VERSION
            or launch.get('github_run_id') != str(RUN) or launch.get('scenario') != 'diagnostic'
            or launch.get('mode') != 'paid' or launch.get('source_commit') != ORIGINAL_SOURCE
            or launch.get('github_workflow_sha') != ORIGINAL_HEAD
            or report.get('reason') != 'agent_protocol_rejection' or report.get('ledger',{}).get('calls') != 5):
        raise ValueError('Original archive is not the reviewed aborted protocol run')
    return {'original_run_id':RUN,'original_source':ORIGINAL_SOURCE,'original_workflow_sha':ORIGINAL_HEAD,
        'artifact_id':ARTIFACT,'zip_sha256':ZIP_SHA256,'manifest_sha256':MANIFEST_SHA256,
        'original_stop_reason':'agent_protocol_rejection','original_generations':5,
        'replacement_source':SOURCE,'scope':'protocol-repair replacement for an aborted run; not an independent replicate'}


def require_origin(evidence):
    expected = {'original_run_id':RUN,'original_source':ORIGINAL_SOURCE,'original_workflow_sha':ORIGINAL_HEAD,
        'artifact_id':ARTIFACT,'zip_sha256':ZIP_SHA256,'manifest_sha256':MANIFEST_SHA256,
        'original_stop_reason':'agent_protocol_rejection','original_generations':5,'replacement_source':SOURCE}
    if not isinstance(evidence,dict) or any(evidence.get(k) != v for k,v in expected.items()):
        raise ValueError('Verified original protocol-failure evidence required')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download_zip(url, token):
    """Authenticate only to GitHub API, never to its signed artifact redirect."""
    request = Request(url,headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json'})
    try:
        response = build_opener(NoRedirect()).open(request,timeout=30)
    except HTTPError as error:
        if error.code != 302:
            raise
        target = error.headers.get('Location','')
        parsed = urlsplit(target)
        error.close()
        if (parsed.scheme != 'https' or parsed.username or parsed.password
                or not parsed.hostname or not (parsed.hostname.endswith('.blob.core.windows.net')
                    or parsed.hostname.endswith('.githubusercontent.com'))):
            raise ValueError('Unexpected artifact-download destination')
        # New request has no Authorization header, cookies or provider secret.
        response = build_opener(NoRedirect()).open(Request(target),timeout=30)
    with response:
        payload = response.read(2_000_001)
    if len(payload) > 2_000_000:
        raise ValueError('Original research ZIP exceeds reviewed download bound')
    return payload


def load_origin(env, output):
    base = 'https://api.github.com/repos/000brittany/consequence-lab/actions/'
    if env.get('GITHUB_REPOSITORY') != '000brittany/consequence-lab':
        raise ValueError('Recovery is scoped to Consequence Lab')
    token = env['ACTIONS_READ_TOKEN']
    def get(path):
        request = Request(base+path,headers={'Authorization':'Bearer '+token,
            'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
        with urlopen(request,timeout=30) as response:
            return json.load(response)
    original_run(get(f'runs/{RUN}'))
    original_artifact(get(f'artifacts/{ARTIFACT}'))
    payload = download_zip(base+f'artifacts/{ARTIFACT}/zip',token)
    evidence = inspect_zip(payload)
    # Preserve a byte-identical copy; neither overwrite nor migrate the origin.
    (output/'original-failed-run.zip').write_bytes(payload)
    (output/'recovery-origin.json').write_text(json.dumps(evidence,indent=2,sort_keys=True)+'\n')
    return evidence
