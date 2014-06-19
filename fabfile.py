from fabric.api import cd
from fabric.api import env
from fabric.api import prefix
from fabric.api import require
from fabric.api import roles
from fabric.api import run
from fabric.api import settings
from fabric.api import sudo
from fabric.api import task
from fabric.operations import prompt
from fabric.contrib.files import exists, upload_template

from contextlib import contextmanager

import fnmatch
import os
import time
from time import gmtime, strftime

"""
Configuration

#CHANGEME
"""

# root
env.root = os.path.abspath(os.path.dirname(__file__))

# project
env.project_name = 'ny-restaurant-inspections'

# paths
# > remote
env.path = '/var/www/apps/%(project_name)s' % env
env.env_path = '%(path)s/env' % env
env.repo_path = '%(path)s/repo' % env
env.rel_path = '%(path)s/rel' % env
env.curr_path = '%(path)s/current' % env

env.pip_req_file = '%(repo_path)s/etc/requirements.txt' % env

# > local
env.maintenance_file = '%(root)s/etc/maintenance.html.tmpl' % env

# config
env.activate = 'source %(env_path)s/bin/activate' % env
env.python = 'python2.7'
env.utc_ts = gmtime()
env.utc_ts_str = strftime('%Y%m%d_%H%M%S', env.utc_ts)

env.user = 'uwsgi-app'
env.password = 'uwsgi-app'
env.git_repo = 'https://github.com/ryankanno/nyc-inspections.git'
env.num_releases = 5
env.cache_buster = ''


# Environments
@task
def production():
    env.settings = 'production'
    env.hosts = ['88.88.88.88']
    env.roledefs.update({'www': ['88.88.88.88']})


@task
def staging():
    env.settings = 'staging'


@task
def local():
    env.settings = 'local'


# Branches
@task
def master():
    env.branch = 'master'


@task
def branch(branch_name):
    env.branch = branch_name


# Task helpers
@contextmanager
def virtualenv():
    with cd(env.env_path):
        with prefix(env.activate):
            yield


def setup_directories():
    with settings(warn_only=True):
        run('mkdir -p %(path)s' % env)
        run('mkdir -p %(env_path)s' % env)
        run('mkdir -p %(repo_path)s' % env)
        run('mkdir -p %(rel_path)s' % env)


def setup_virtualenv():
    if not exists('%(env_path)s/bin' % env):
        run('virtualenv -p %(python)s --no-site-packages %(env_path)s;' % env)
        with virtualenv():
            run('easy_install -U setuptools')
            run('easy_install pip')


def install_requirements():
    """ Install pip requirements.txt """
    with virtualenv():
        run('pip install -r %(pip_req_file)s' % env)


def clone_repo():
    """ Clone repo """
    if not exists('%(repo_path)s/.git' % env):
        run('git clone %(git_repo)s %(repo_path)s' % env)


def checkout():
    """ Checkout """
    with cd(env.repo_path):
        run('git checkout %(branch)s; \
             git pull origin %(branch)s; git submodule update --init' % env)


def service(name, *actions):
    """ Generic service function """
    for action in actions:
        sudo('/etc/init.d/%s %s' % (name, action))


def copy_to_releases():
    run('cp -R %(repo_path)s %(rel_path)s/%(utc_ts_str)s' % env)
    run('rm -rf %(rel_path)s/%(utc_ts_str)s/.git*' % env)


def configure_app():
    pass


def symlink_release():
    """ Symlink the current release """
    milliseconds_since_epoch = int(round(time.time() * 1000))
    curr_tmp = '%s_%s' % (env.curr_path, milliseconds_since_epoch)

    run('ln -s %s %s && mv -Tf %s %s' %
        (get_latest_release(),
         curr_tmp,
         curr_tmp,
         '%(curr_path)s' % env))


def get_sorted_releases():
    """ Returns the list of releases sorted """
    with cd('%(rel_path)s' % env):
        return sorted(run('ls -xt').split())


def get_latest_release():
    releases = get_sorted_releases()
    return '%s/%s' % (env.rel_path, releases[-1])


def remove_latest_release():
    latest_release = get_latest_release()
    with cd('%(rel_path)s' % env):
        run('rm -rf %s' % latest_release)


def keep_num_releases(num_releases):
    releases = get_sorted_releases()
    num_to_delete = len(releases) - num_releases

    # Must keep a minimum of one release
    if num_to_delete > 1:
        del_releases = releases[:num_to_delete]
        with cd('%(rel_path)s' % env):
            for release in del_releases:
                run('rm -rf %s' % release)


def find_files(directory, pattern):
    for root, dirs, files in os.walk(directory):
        for basename in files:
            if fnmatch.fnmatch(basename, pattern):
                yield os.path.join(root, basename)


# Setup
@task
def configure_www(file):
    """ Configure the Nginx www server"""
    require('settings', provided_by=[production, staging, local])
    context = {
        'server_name': env.project_name,
        'curr_path': env.curr_path,
    }
    upload_template(
        file,
        '/etc/nginx/nginx.conf',
        context=context, use_sudo=True)

    www('restart')


@task
def configure_uwsgi(file):
    """ Configure UWSGI app server"""
    require('settings', provided_by=[production, staging, local])
    upload_template(
        file,
        '/etc/uwsgi/apps-available/%(project_name)s' % env,
        use_sudo=True)

    sudo('ln -fs /etc/uwsgi/apps-available/%(project_name)s \
          /etc/uwsgi/apps-enabled/%(project_name)s.xml' % env)
    app('restart')


# Release
@task
def setup():
    require('settings', provided_by=[production, staging, local])
    require('branch', provided_by=[master, branch])

    setup_directories()
    setup_virtualenv()
    clone_repo()
    checkout()
    install_requirements()


@task
def deploy(with_maintenance=False, update_requirements=False):
    """ Checkout, configure, symlink release """
    require('settings', provided_by=[production, staging, local])
    require('branch', provided_by=[master, branch])

    if with_maintenance:
        with settings(warn_only=True):
            maintenance_up()

    checkout()
    copy_to_releases()

    if update_requirements:
        install_requirements()

    configure_app()
    symlink_release()


@task
def rollback():
    """ Removes latest release and repoints current symlink
        to the previous release """
    require('settings', provided_by=[production, staging, local])

    remove_latest_release()
    symlink_release()


@task
def cleanup(num_releases=0):
    """ Cleans up deploy directory, keeping N number of releases """
    require('settings', provided_by=[production, staging, local])

    keep_num_releases(num_releases or env.num_releases)


@task
def maintenance_up():
    """ Prompts for a reason why we are down, then deploys maintenance page """
    ctx = {}
    ctx['reason'] = prompt("Why are we downs?")
    upload_template(
        '%(maintenance_file)s' % env,
        '%(curr_path)s/maintenance.html' % env,
        context=ctx, backup=False)


@task
def maintenance_down():
    """ Removes maintenance page """
    if exists('%(curr_path)s/maintenance.html' % env):
        run('rm %(curr_path)s/maintenance.html' % env)


# Maintenance
@task
@roles('www')
def www(action):
    service('nginx', action)


@task
@roles('www')
def app(action):
    service('uwsgi', action)


@task
@roles('www')
def cache(action):
    if action == 'purge':
        run('redis-cli FLUSHALL')
