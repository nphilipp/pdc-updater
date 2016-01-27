import logging
import requests

import pdcupdater.handlers
import pdcupdater.services
import pdcupdater.utils

from pdc_client import get_paged


log = logging.getLogger(__name__)


class AtomicComponentGroupHandler(pdcupdater.handlers.BaseHandler):
    """ When someone changes the packages list for fedora-atomic.

    https://git.fedorahosted.org/cgit/fedora-atomic.git/tree/fedora-atomic-docker-host.json

    """
    group_type = 'atomic-docker-host'

    def __init__(self, *args, **kwargs):
        super(AtomicComponentGroupHandler, self).__init__(*args, **kwargs)
        self.git_url = self.config['pdcupdater.fedora_atomic_git_url']

    @property
    def topic_suffixes(self):
        return [
            'trac.git.receive',
        ]

    def can_handle(self, msg):
        if not msg['topic'].endswith('trac.git.receive'):
            return False
        if msg['msg']['commit']['repo'] != 'fedora-atomic':
            return False
        return True

    def atomic_component_groups_from_git(self, pdc):
        # First, build a mapping of git branches (from the fedora-atomic
        # fedorahosted repo) to PDC release ids.
        tags = [pdcupdater.utils.rawhide_tag()]
        for release in pdcupdater.utils.bodhi_releases():
            if 'EPEL' in release['id_prefix']:
                # We don't maintain an atomic group for epel.
                continue
            tags.append(release['stable_tag'])

        pdc_releases = [pdcupdater.utils.tag2release(tag) for tag in tags]
        for release_id, release in pdc_releases:
            # First, make sure PDC can handle a group on this release
            pdcupdater.utils.ensure_release_exists(pdc, release_id, release)

            # Then, map the fedorahosted git repo branch to our PDC release
            if release['release_type'] == 'ga':
                branch = 'master'
            else:
                branch = 'f' + release['version']

            # Go, get, and parse the data
            params = dict(h=branch)
            filename = 'fedora-%s.json' % self.group_type
            response = requests.get(self.git_url + filename, params=params)
            data = response.json()

            # And return formatted component group data
            packages = data['packages']
            yield {
                'group_type': self.group_type,
                'release': release_id,
                'description': 'Deps for %s %s' % (
                    self.group_type,
                    self.git_url,
                ),
                'components': [{
                    'release': release_id,
                    'name': package,
                } for package in packages],
            }

    def handle(self, pdc, msg):
        component_groups = self.atomic_component_groups_from_git(pdc)
        for group in component_groups:
            self._update_atomic_component_group(pdc, group)

    def audit(self, pdc):
        # Query the data sources
        git_group = self.atomic_component_group_from_git()
        pdc_groups = get_paged(pdc['component-groups']._)
        pdc_group = [
            group for group in pdc_groups
            if group['group_type'] == self.group_type
        ]

        # normalize the two lists
        git_group = set(git_group['components'])
        pdc_group = set(pdc_group['components'])

        # use set operators to determine the difference
        present = pdc_group - git_group
        absent = git_group - pdc_group

        return present, absent

    def initialize(self, pdc):
        component_groups = self.atomic_component_groups_from_git(pdc)
        for group in component_groups:
            self._update_atomic_component_group(pdc, group)

    def _update_atomic_component_group(self, pdc, component_group):
        # Make sure our pre-requisites exist
        pdcupdater.utils.ensure_component_group_exists(pdc, component_group)
        for component in component_group['components']:
            pdcupdater.utils.ensure_release_component_exists(
                pdc, component['release'], component['name'])

        # Figure out the primary key for this group we have here..
        group_pk = pdcupdater.utils.get_group_pk(pdc, component_group)

        # And perform the update with a PUT
        pdc['component-groups'][group_pk]._ = component_group