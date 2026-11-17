"""Read-only Maintainerr safety inspector for rule-owned TV show cleanup.

No mutating endpoints. In particular never call GET /api/collections/deactivate:
that *writes* despite the HTTP verb. Never call POST /api/rules/test:
it flushes caches. Read private host APIs only from trusted operator config.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import re
import sys
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError


class EvidenceUnavailable(Exception):
    pass


def require(valid, reason):
    if not valid:
        raise EvidenceUnavailable(reason)


class RefuseRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class FixedReadClient:
    """Trusted fixed local endpoints; no caller-controlled paths or methods."""
    def __init__(self, root, key=None, plex=False):
        p = urlsplit(root)
        require(p.scheme in ('http', 'https') and p.hostname and not (
            p.username or p.password or p.query or p.fragment or p.path not in ('', '/')
        ), 'invalid_private_source')
        self.root = root.rstrip('/')
        self.key = key
        self.plex = plex
        require(not plex or isinstance(key, str) and len(key) > 0, 'missing_plex_credential')

    def get(self, path):
        require(path.startswith('/') and not path.startswith('//') and
                '\n' not in path and '\r' not in path, 'invalid_read_path')
        headers = {'Accept': 'application/json'}
        if self.plex:
            headers['X-Plex-Token'] = self.key
        req = Request(self.root + path, headers=headers, method='GET')
        try:
            with build_opener(RefuseRedirect()).open(req, timeout=10) as response:
                require(response.status == 200, 'read_unavailable')
                raw = response.read(2_000_001)
            require(0 < len(raw) <= 2_000_000, 'invalid_response_size')
            require(not self.key or self.key.encode() not in raw, 'credential_echo')
            return json.loads(raw)
        except (HTTPError, URLError, OSError, TimeoutError, ValueError) as exc:
            raise EvidenceUnavailable('read_unavailable') from None


def find_plex_show(client, library_id, title):
    require(type(library_id) is int and library_id > 0 and
            isinstance(title, str) and 1 <= len(title) <= 90, 'invalid_selector')
    matches = []
    total = None
    for offset in range(0, 10000, 100):
        data = client.get(
            f'/library/sections/{library_id}/all?type=2&includeGuids=1'
            f'&X-Plex-Container-Start={offset}&X-Plex-Container-Size=100'
        ).get('MediaContainer')
        require(isinstance(data, dict), 'invalid_plex_response')
        rows = data.get('Metadata', [])
        count = data.get('totalSize')
        require(isinstance(rows, list) and len(rows) <= 100 and
                type(count) is int and count <= 10000 and
                data.get('size') == len(rows) and
                (total is None or total == count) and
                data.get('offset', 0) == offset, 'incomplete_plex_library')
        total = count
        for row in rows:
            if row.get('title', '').casefold().strip() == title.casefold().strip():
                matches.append(row)
        if offset + len(rows) == total:
            break
        require(len(rows) == 100, 'incomplete_plex_library')
    require(len(matches) == 1 and str(matches[0].get('ratingKey', '')).isdigit(),
            'plex_show_missing_or_ambiguous')
    return matches[0]


def parse_utc(raw):
    require(isinstance(raw, str) and len(raw) <= 40, 'invalid_membership_date')
    try:
        dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        require(dt.tzinfo is not None, 'invalid_membership_date')
        return dt.astimezone(timezone.utc)
    except ValueError:
        raise EvidenceUnavailable('invalid_membership_date') from None


def summarize_membership(plex_row, collections):
    """Pure, conservative correlation; no media state change or API calls."""
    require(isinstance(collections, list) and len(collections) <= 400,
            'invalid_collection_listing')
    plex_id = str(plex_row['ratingKey'])
    matches = []
    for collection in collections:
        require(isinstance(collection, dict), 'invalid_collection')
        memberships = collection.get('media')
        require(isinstance(memberships, list) and len(memberships) <= 20000,
                'incomplete_collection_members')
        for member in memberships:
            if not isinstance(member, dict) or str(member.get('mediaServerId')) != plex_id:
                continue
            days = collection.get('deleteAfterDays')
            require(days is None or type(days) is int and 0 <= days <= 36500,
                    'unknown_deletion_window')
            added = parse_utc(member.get('addDate'))
            deadline = added + timedelta(days=days or 0)
            settings_id = collection.get('id')
            require(type(settings_id) is int and settings_id > 0, 'invalid_collection_id')
            matches.append({
                'collection_id': settings_id,
                'collection_name': str(collection.get('name') or 'unnamed')[:150],
                'rule_group_id': collection.get('ruleGroupId'),
                'add_date_utc': added.isoformat(),
                'delete_after_days': days,
                'handler_due_utc': deadline.isoformat(),
                'timer_note': ('null deletion window is due immediately in handler'
                               if days is None else 'membership addDate + deleteAfterDays'),
                'included_by_rule': member.get('includedByRule'),
                'manual_source': member.get('manualMembershipSource'),
                'rule_evaluation_failed': member.get('ruleEvaluationFailed'),
            })
    return matches


def inspect(client, plex, title='Lanterns', tv_library_id=2):
    show = find_plex_show(plex, tv_library_id, title)
    # overlay-data reads only Maintainerr's own DB, no metadata-enrichment GETs.
    collections = client.get('/api/collections/overlay-data')
    matched = summarize_membership(show, collections)
    for hit in matched:
        cid = hit['collection_id']
        # The following GETs are true reads. DO NOT request /api/rules or
        # /api/rules/{id}: those include unmasked notification credentials.
        settings = client.get(f'/api/collections/collection/{cid}')
        require(isinstance(settings, dict) and settings.get('id') == cid,
                'collection_settings_unavailable')
        hit['action_setting'] = settings.get('arrAction')
        hit['collection_is_active'] = settings.get('isActive')
        hit['collection_type'] = settings.get('typeId')
        gid = hit['rule_group_id']
        if type(gid) is int and gid > 0:
            rules = client.get(f'/api/rules/{gid}/rules')
            require(isinstance(rules, list) and len(rules) <= 200,
                    'rules_unavailable')
            hit['rules'] = [{
                'section': r.get('section'), 'enabled': r.get('isActive'),
                'operator': (json.loads(r.get('ruleJson', '{}')).get('operator')),
                'first_value': str(json.loads(r.get('ruleJson', '{}')).get('firstVal'))[:100],
                'action': str(json.loads(r.get('ruleJson', '{}')).get('action'))[:80],
                'has_custom_value': 'customVal' in json.loads(r.get('ruleJson', '{}'))
            } for r in rules if isinstance(r, dict)]
        else:
            hit['rules'] = 'rule_group_identity_unavailable'
    return {
        'schema_version': '0.1', 'source': 'read_only_maintainerr_and_plex_get',
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'show': {'title': show.get('title'), 'plex_rating_key': str(show['ratingKey'])},
        'matching_collections': matched,
        'status': 'evidence_collected_rule_match_cause_not_proven' if matched else
                  'not_found_in_maintainerr_collection_snapshot',
        'write_performed': False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--title', default='Lanterns')
    args = parser.parse_args()
    env = lambda k: os.environ[k]
    result = inspect(FixedReadClient(env('MAINTAINERR_BASE_URL')),
                     FixedReadClient(env('PLEX_BASE_URL'), env('PLEX_TOKEN'), plex=True),
                     title=args.title, tv_library_id=int(env('PLEX_TV_LIBRARY_ID')))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # No upstream response body, path, token or traceback may escape.
        print(json.dumps({'status': 'blocked', 'reason': 'evidence_unavailable',
                          'write_performed': False}))
        sys.exit(2)
