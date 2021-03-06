# No shebang line, this module is meant to be imported
#
# Copyright 2013 Oliver Palmer
# Copyright 2014 Ambient Entertainment GmbH & Co. KG
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Jobs
----

This module defines an API for managing and querying jobs
"""

from decimal import Decimal
from json import loads
from datetime import datetime

try:
    from httplib import (
        OK, BAD_REQUEST, NOT_FOUND, INTERNAL_SERVER_ERROR, CREATED, NO_CONTENT)
except ImportError:  # pragma: no cover
    from http.client import (
        OK, BAD_REQUEST, NOT_FOUND, INTERNAL_SERVER_ERROR, CREATED, NO_CONTENT)

from flask.views import MethodView
from flask import g, request

from sqlalchemy.sql import func, or_

from pyfarm.core.logger import getLogger
from pyfarm.core.enums import STRING_TYPES, NUMERIC_TYPES, WorkState, _WorkState
from pyfarm.scheduler.tasks import (
    assign_tasks_to_agent, assign_tasks, delete_job)
from pyfarm.models.statistics.task_event_count import TaskEventCount
from pyfarm.models.jobtype import JobType, JobTypeVersion
from pyfarm.models.task import Task
from pyfarm.models.user import User
from pyfarm.models.job import Job, JobNotifiedUser
from pyfarm.models.software import (
    Software, SoftwareVersion, JobSoftwareRequirement)
from pyfarm.models.tag import Tag, JobTagRequirement
from pyfarm.models.jobqueue import JobQueue
from pyfarm.models.agent import Agent
from pyfarm.master.application import db
from pyfarm.master.utility import (
    jsonify, validate_with_model, get_request_argument)
from pyfarm.master.config import config

RANGE_TYPES = NUMERIC_TYPES[:-1] + (Decimal, )

try:
  # pylint: disable=undefined-variable
  range_ = xrange
except NameError:
  range_ = range

logger = getLogger("api.jobs")

# Load model mappings once per process
TASK_MODEL_MAPPINGS = Task.types().mappings
AUTOCREATE_USERS = config.get("autocreate_users")
AUTO_USER_EMAIL = config.get("autocreate_user_email")
DEFAULT_JOB_DELETE_TIME = config.get("default_job_delete_time")


class ObjectNotFound(Exception):
    pass


def parse_requirements(requirements):
    """
    Takes a list dicts specifying a software and optional min- and max-versions
    and returns a list of :class:`JobRequirement` objects.

    Raises TypeError if the input was not as expected or ObjectNotFound if a
    referenced software or version was not found.

    :param list requirements:
        A list of of dicts specifying a software and optionally min_version
        and/or max_version.

    :raises TypeError:
        Raised if ``requirements`` is not a list or if an entry in
        ``requirements`` is not a dictionary.

    :raises ValueError:
        Raised if there's a problem with the content of at least one of the
        requirement dictionaries.

    :raises ObjectNotFound:
        Raised if the referenced software or version was not found
    """
    if not isinstance(requirements, list):
        raise TypeError("software_requirements must be a list")

    out = []
    for entry in requirements:
        if not isinstance(entry, dict):
            raise TypeError("Every software_requirement must be a dict")

        requirement = JobSoftwareRequirement()
        software_name = entry.pop("software", None)
        if software_name is None:
            raise ValueError("Software requirement does not specify a software.")
        software = Software.query.filter_by(software=software_name).first()
        if not software:
            raise ObjectNotFound("Software %s not found" % software_name)
        requirement.software = software

        min_version_str = entry.pop("min_version", None)
        if min_version_str is not None:
            min_version = SoftwareVersion.query.filter(
                SoftwareVersion.software == software,
                SoftwareVersion.version == min_version_str).first()
            if not min_version:
                raise ObjectNotFound("Version %s of software %s not found" %
                                        (min_version_str, software_name))
            requirement.min_version = min_version

        max_version_str = entry.pop("max_version", None)
        if max_version_str is not None:
            max_version = SoftwareVersion.query.filter(
                SoftwareVersion.software == software,
                SoftwareVersion.version == max_version_str).first()
            if not max_version:
                raise ObjectNotFound("Version %s of software %s not found" %
                                     (max_version_str, software_name))
            requirement.max_version = max_version

        if entry:
            raise ValueError("Unexpected keys in software requirement: %r" %
                            entry.keys())

        out.append(requirement)
    return out


def schema():
    """
    Returns the basic schema of :class:`.Job`

    .. http:get:: /api/v1/jobs/schema HTTP/1.1

        **Request**

        .. sourcecode:: http

            GET /api/v1/jobs/schema HTTP/1.1
            Accept: application/json

        **Response**

        .. sourcecode:: http

            HTTP/1.1 200 OK
            Content-Type: application/json

            {
                "batch": "INTEGER",
                "by": "NUMERIC(10, 4)",
                "cpus": "INTEGER",
                "data": "JSONDict",
                "end": "NUMERIC(10,4)",
                "environ": "JSONDict",
                "hidden": "BOOLEAN",
                "id": "INTEGER",
                "jobtype": "VARCHAR(64)",
                "jobtype_version": "INTEGER",
                "jobqueue": "VARCHAR(255)",
                "notes": "TEXT",
                "priority": "INTEGER",
                "project_id": "INTEGER",
                "ram": "INTEGER",
                "ram_max": "INTEGER",
                "ram_warning": "INTEGER",
                "requeue": "INTEGER",
                "start": "NUMERIC(10,4)",
                "state": "WorkStateEnum",
                "time_finished": "DATETIME",
                "time_started": "DATETIME",
                "time_submitted": "DATETIME",
                "title": "VARCHAR(255)",
                "user": "VARCHAR(255)"
            }

    :statuscode 200: no error
    """
    schema_dict = Job.to_schema()

    # These columns are not part of the actual database model, but will be
    # dynamically computed by the api
    schema_dict["start"] = "NUMERIC(10,4)"
    schema_dict["end"] = "NUMERIC(10,4)"
    schema_dict["user"] = "VARCHAR(%s)" % config.get("max_username_length")
    schema_dict["jobqueue"] = \
        "VARCHAR(%s)" % config.get("max_queue_name_length")

    # In the database, we are storing the jobtype_version_id, but over the wire,
    # we are using the jobtype's name plus version to identify it
    del schema_dict["jobtype_version_id"]
    # Same for user_id
    del schema_dict["user_id"]
    # jobqueue too
    del schema_dict["job_queue_id"]
    schema_dict["jobtype"] = \
        "VARCHAR(%s)" % config.get("job_type_max_name_length")
    schema_dict["jobtype_version"] = "INTEGER"
    return jsonify(schema_dict), OK


class JobIndexAPI(MethodView):
    @validate_with_model(Job,
                         type_checks={"by": lambda x: isinstance(
                             x, RANGE_TYPES)},
                         ignore=["start", "end", "jobtype", "jobtype_version",
                                 "user", "jobqueue", "tag_requirements"],
                         disallow=["jobtype_version_id", "time_submitted",
                                   "time_started", "time_finished",
                                   "job_queue_id"])
    def post(self):
        """
        A ``POST`` to this endpoint will submit a new job.

        .. http:post:: /api/v1/jobs/ HTTP/1.1

            **Request**

            .. sourcecode:: http

                POST /api/v1/jobs/ HTTP/1.1
                Accept: application/json

                {
                    "end": 2.0,
                    "title": "Test Job 2",
                    "jobtype": "TestJobType",
                    "data": {
                        "foo": "bar"
                    },
                    "software_requirements": [
                        {
                        "software": "blender"
                        }
                    ],
                    "start": 1.0
                }

            **Response**

            .. sourcecode:: http

                HTTP/1.1 201 CREATED
                Content-Type: application/json

                {
                    "time_finished": null,
                    "time_started": null,
                    "end": 2.0,
                    "time_submitted": "2014-03-06T15:40:58.335259",
                    "jobtype_version": 1,
                    "jobtype": "TestJobType",
                    "jobqueue": None
                    "start": 1.0,
                    "priority": 0,
                    "state": "queued",
                    "parents": [],
                    "hidden": false,
                    "project_id": null,
                    "ram_warning": null,
                    "title": "Test Job 2",
                    "tags": [],
                    "user": null,
                    "by": 1.0,
                    "data": {
                        "foo": "bar"
                    },
                    "ram_max": null,
                    "notes": "",
                    "batch": 1,
                    "project": null,
                    "environ": null,
                    "requeue": 3,
                    "software_requirements": [
                        {
                            "min_version": null,
                            "max_version": null,
                            "max_version_id": null,
                            "software_id": 1,
                            "min_version_id": null,
                            "software": "blender"
                        }
                    ],
                    "tag_requirements": [
                        {
                            "tag": "workstation",
                            "negate": true
                        }
                    ],
                    "id": 2,
                    "ram": 32,
                    "cpus": 1,
                    "children": []
                }

        :statuscode 201: a new job item was created
        :statuscode 400: there was something wrong with the request (such as
                            invalid columns being included)
        :statuscode 404: a referenced object, like a software or software
                            version, does not exist
        :statuscode 409: a conflicting job already exists
        """
        if "jobtype" not in g.json:
            return jsonify(error="No jobtype specified"), BAD_REQUEST
        if not isinstance(g.json["jobtype"], STRING_TYPES):
            return jsonify(error="jobtype must be of type string"), BAD_REQUEST

        q = JobTypeVersion.query.filter(
                JobTypeVersion.jobtype.has(JobType.name == g.json["jobtype"]))
        del g.json["jobtype"]
        if "jobtype_version" in g.json:
            if not isinstance(g.json["jobtype_version"], int):
                return (jsonify(error="jobtype_version must be of type int"),
                        BAD_REQUEST)
            q = q.filter(JobTypeVersion.version == g.json["jobtype_version"])
            del g.json["jobtype_version"]
        jobtype_version = q.order_by("version desc").first()

        if not jobtype_version:
            return jsonify("Jobtype or version not found"), NOT_FOUND

        software_requirements = []
        if "software_requirements" in g.json:
            try:
                software_requirements = parse_requirements(
                    g.json["software_requirements"])
            except (TypeError, ValueError) as e:
                return jsonify(error=e.args), BAD_REQUEST
            except ObjectNotFound as e:
                return jsonify(error=e.args), NOT_FOUND
            del g.json["software_requirements"]

        parents = []
        if "parents" in g.json:
            for parent_job_data in g.json["parents"]:
                parent_job = Job.query.filter_by(
                    id=parent_job_data["id"]).first()
                if not parent_job:
                    return (jsonify("Parent job %s not found" %
                                    parent_job_data["id"] ), NOT_FOUND)
                parents.append(parent_job)
            del g.json["parents"]

        tag_names = g.json.pop("tags", None)
        tags = []
        if tag_names:
            for tag_name in tag_names:
                tag = Tag.query.filter_by(tag=tag_name).first()
                if not tag:
                    tag = Tag(tag=tag_name)
                tags.append(tag)

        user = None
        username = g.json.pop("user", None)
        if username:
            user = User.query.filter_by(username=username).first()
            if not user and AUTOCREATE_USERS:
                user = User(username=username)
                if AUTO_USER_EMAIL:
                    user.email = AUTO_USER_EMAIL.format(username=username)
                db.session.add(user)
                logger.warning("User %s was autocreated on job submit", username)
            elif not user:
                return (jsonify(
                    error="User %s not found" % username), NOT_FOUND)

        jobqueue = None
        jobqueue_name = g.json.pop("jobqueue", None)
        if jobqueue_name:
            path_elements = jobqueue_name.split("/")
            for element in path_elements:
                jobqueue = JobQueue.query.filter_by(
                    parent=jobqueue, name=element).first()
                if not jobqueue:
                    return (jsonify(error="Jobqueue %s not found" %
                                    jobqueue_name),
                            NOT_FOUND)

        notified_usernames = g.json.pop("notified_users", None)
        tag_requirements = g.json.pop("tag_requirements", None)

        g.json.pop("start", None)
        g.json.pop("end", None)
        job = Job(**g.json)
        job.jobtype_version = jobtype_version
        job.software_requirements = software_requirements
        job.parents = parents
        job.tags = tags
        job.user = user
        job.queue = jobqueue
        job.autodelete_time = g.json.get("autodelete_time",
                                         DEFAULT_JOB_DELETE_TIME)

        if notified_usernames:
            for entry in notified_usernames:
                user = User.query.filter_by(username=entry["username"]).first()
                if not user and AUTOCREATE_USERS:
                    username = entry["username"]
                    user = User(username=username)
                    if AUTO_USER_EMAIL:
                        user.email = AUTO_USER_EMAIL.format(username=username)
                    db.session.add(user)
                    db.session.flush()
                    logger.warning("User %s was autocreated on job submit",
                                   username)
                elif not user:
                    return (jsonify(
                                error="User %s not found" % entry["username"]),
                            NOT_FOUND)
                notified_user = JobNotifiedUser(user=user, job=job)
                if "on_success" in entry:
                    notified_user.on_success = entry["on_success"]
                if "on_failure" in entry:
                    notified_user.on_failure = entry["on_failure"]
                if "on_deletion" in entry:
                    notified_user.on_deletion = entry["on_deletion"]
                db.session.add(notified_user)

        if tag_requirements:
            for entry in tag_requirements:
                tag = Tag.query.filter_by(tag=entry["tag"]).first()
                if not tag:
                    tag = Tag(tag=entry["tag"])
                    db.session.add(tag)
                tag_requirement = JobTagRequirement(job=job, tag=tag)
                if entry["negate"]:
                    tag_requirement.negate = True
                db.session.add(tag_requirement)

        custom_json = loads(request.data.decode(), parse_float=Decimal)
        if "end" in custom_json and "start" not in custom_json:
            return (jsonify(error="`end` is specified while `start` is not"),
                    BAD_REQUEST)
        start = custom_json.get("start", Decimal("1.0"))
        end = custom_json.get("end", start)
        if (not isinstance(start, RANGE_TYPES) or
            not isinstance(end, RANGE_TYPES)):
            return (jsonify(error="`start` and `end` need to be of type decimal "
                                    "or int"), BAD_REQUEST)

        if not end >= start:
            return (jsonify(error="`end` must be larger than or equal to start"),
                    BAD_REQUEST)

        by = custom_json.pop("by", Decimal("1.0"))
        if not isinstance(by, RANGE_TYPES):
            return (jsonify(error="`by` needs to be of type decimal or int"),
                    BAD_REQUEST)

        num_tiles = g.json.get("num_tiles", None)
        if not jobtype_version.supports_tiling and num_tiles is not None:
            return (jsonify(error="`num_tiles` is set, but this "
                                  "jobtype does not support tiling."),
                    BAD_REQUEST)

        job.alter_frame_range(start, end, by)

        db.session.add(job)
        db.session.add_all(software_requirements)
        db.session.commit()
        job_data = job.to_dict(unpack_relationships=["tags",
                                                     "data",
                                                     "software_requirements",
                                                     "parents",
                                                     "children",
                                                     "notified_users",
                                                     "tag_requirements"])
        job_data["start"] = start
        job_data["end"] = end
        del job_data["jobtype_version_id"]
        job_data["jobtype"] = job.jobtype_version.jobtype.name
        job_data["jobtype_version"] = job.jobtype_version.version
        job_data["user"] = job.user.username if job.user else None
        del job_data["user_id"]
        job_data["jobqueue"] = job.queue.path() if job.queue else None
        del job_data["job_queue_id"]
        job_data["jobgroup"] = job.group.title if job.group else None
        if job.state is None:
            num_assigned_tasks = Task.query.filter(Task.job == job,
                                                   Task.agent != None).count()
            if num_assigned_tasks > 0:
                job_data["state"] = "running"
            else:
                job_data["state"] = "queued"

        logger.info("Created new job %r", job_data)
        assign_tasks.delay()

        return jsonify(job_data), CREATED

    def get(self):
        """
        A ``GET`` to this endpoint will return a list of all jobs.

        .. http:get:: /api/v1/jobs/ HTTP/1.1

            **Request**

            .. sourcecode:: http

                GET /api/v1/jobs/ HTTP/1.1
                Accept: application/json

            **Response**

            .. sourcecode:: http

                HTTP/1.1 200 OK
                Content-Type: application/json

                [
                    {
                        "title": "Test Job",
                        "state": "queued",
                        "id": 1
                    },
                    {
                        "title": "Test Job 2",
                        "state": "queued",
                        "id": 2
                    }
                ]

        :statuscode 200: no error
        """

        jobtype_name = get_request_argument("jobtype")
        user_name = get_request_argument("user")
        job_title = get_request_argument("title")

        jobqueue_names = []
        if "jobqueue" in request.args:
            jobqueue_names = request.args.getlist("jobqueue")

        out = []
        subq = db.session.query(
            Task.job_id,
            func.count(Task.id).label('assigned_tasks_count')).\
                filter(Task.agent_id != None).group_by(Task.job_id).subquery()
        q = db.session.query(Job.id, Job.title, Job.state,
                             subq.c.assigned_tasks_count).\
            outerjoin(subq, Job.id == subq.c.job_id)

        if jobtype_name is not None:
            jobtype = JobType.query.filter_by(name=jobtype_name).first()
            if not jobtype:
                return (jsonify(error="Jobtype %s not found" % jobtype_name),
                        NOT_FOUND)
            q = q.join(JobTypeVersion,
                       Job.jobtype_version_id == JobTypeVersion.id)
            q = q.filter(JobTypeVersion.jobtype == jobtype)

        if user_name is not None:
            user = User.query.filter_by(username=user_name).first()
            if not user:
                return (jsonify(error="User %s not found" % user_name),
                        NOT_FOUND)
            q = q.filter(Job.user == user)

        if jobqueue_names:
            jobqueue_ids = []
            for jobqueue_name in jobqueue_names:
                jobqueue = JobQueue.query.filter_by(
                    fullpath=jobqueue_name).first()
                if not jobqueue:
                    return (
                        jsonify(error="Jobqueue %s not found" % jobqueue_name),
                        NOT_FOUND)
                jobqueue_ids.append(jobqueue.id)
            q = q.filter(Job.job_queue_id.in_(jobqueue_ids))

        if job_title:
            q = q.filter(Job.title.ilike("%%%s%%" % job_title))

        for id, title, state, assigned_tasks_count in q:
            data = {"id": id, "title": title}
            if state is None and not assigned_tasks_count:
                data["state"] = "queued"
            elif state is None:
                data["state"] = "assigned"
            else:
                data["state"] = str(state)
            out.append(data)

        return jsonify(out), OK


class SingleJobAPI(MethodView):
    def get(self, job_name):
        """
        A ``GET`` to this endpoint will return the specified job, by name or id.

        .. http:get:: /api/v1/jobs/[<str:name>|<int:id>] HTTP/1.1

            **Request**

            .. sourcecode:: http

                GET /api/v1/jobs/Test%20Job%202 HTTP/1.1
                Accept: application/json

            **Response**

            .. sourcecode:: http

                HTTP/1.1 200 OK
                Content-Type: application/json

                {
                    "ram_warning": null,
                    "title": "Test Job",
                    "state": "queued",
                    "jobtype_version": 1,
                    "jobtype": "TestJobType",
                    "environ": null,
                    "user": null,
                    "priority": 0,
                    "time_finished": null,
                    "start": 2.0,
                    "id": 1,
                    "notes": "",
                    "notified_users": []
                    "ram": 32,
                    "tags": [],
                    "hidden": false,
                    "data": {
                        "foo": "bar"
                    },
                    "software_requirements": [
                        {
                            "software": "blender",
                            "software_id": 1,
                            "min_version": null,
                            "max_version": null,
                            "min_version_id": null,
                            "max_version_id": null
                        }
                    ],
                    "tag_requirements": [
                        {
                            "tag": "workstation",
                            "negate": true
                        }
                    ],
                    "batch": 1,
                    "time_started": null,
                    "time_submitted": "2014-03-06T15:40:58.335259",
                    "requeue": 3,
                    "end": 4.0,
                    "parents": [],
                    "cpus": 1,
                    "ram_max": null,
                    "children": [],
                    "by": 1.0,
                    "project_id": null
                }

        :statuscode 200: no error
        :statuscode 404: job not found
        """
        if isinstance(job_name, STRING_TYPES):
            job = Job.query.filter_by(title=job_name).first()
        else:
            job = Job.query.filter_by(id=job_name).first()

        if not job:
            return jsonify(error="Job not found"), NOT_FOUND

        job_data = job.to_dict(unpack_relationships=["tags",
                                                     "data",
                                                     "software_requirements",
                                                     "parents",
                                                     "children",
                                                     "notified_users",
                                                     "tag_requirements"])

        first_task = Task.query.filter_by(job=job).order_by("frame asc").first()
        last_task = Task.query.filter_by(job=job).order_by("frame desc").first()

        if not first_task or not last_task: # pragma: no cover
            return (jsonify(error="Job does not have any tasks"),
                    INTERNAL_SERVER_ERROR)

        job_data["start"] = first_task.frame
        job_data["end"] = last_task.frame
        job_data["jobtype"] = job.jobtype_version.jobtype.name
        job_data["jobtype_version"] = job.jobtype_version.version
        job_data["user"] = job.user.username if job.user else None
        del job_data["user_id"]
        job_data["jobqueue"] = job.queue.path() if job.queue else None
        del job_data["job_queue_id"]
        job_data["jobgroup"] = job.group.title if job.group else None
        if job.state is None:
            num_assigned_tasks = Task.query.filter(Task.job == job,
                                                   Task.agent != None).count()
            if num_assigned_tasks > 0:
                job_data["state"] = "running"
            else:
                job_data["state"] = "queued"

        del job_data["jobtype_version_id"]

        return jsonify(job_data), OK

    def post(self, job_name):
        """
        A ``POST`` to this endpoint will update the specified job with the data
        in the request.  Columns not specified in the request will be left as
        they are.
        If the "start", "end" or "by" columns are updated, tasks will be created
        or deleted as required.

        .. http:post:: /api/v1/jobs/[<str:name>|<int:id>] HTTP/1.1

            **Request**

            .. sourcecode:: http

                PUT /api/v1/jobs/Test%20Job HTTP/1.1
                Accept: application/json

                {
                    "start": 2.0
                }

            **Response**

            .. sourcecode:: http

                HTTP/1.1 201 OK
                Content-Type: application/json

                {
                    "end": 4.0,
                    "children": [],
                    "jobtype_version": 1,
                    "jobtype": "TestJobType",
                    "time_started": null,
                    "tasks_failed": [],
                    "project_id": null,
                    "id": 1,
                    "software_requirements": [
                        {
                            "software": "blender",
                            "min_version": null,
                            "max_version_id": null,
                            "software_id": 1,
                            "max_version": null,
                            "min_version_id": null
                        }
                    ],
                    "tags": [],
                    "environ": null,
                    "requeue": 3,
                    "start": 2.0,
                    "ram_warning": null,
                    "title": "Test Job",
                    "batch": 1,
                    "time_submitted": "2014-03-06T15:40:58.335259",
                    "ram_max": null,
                    "user": null,
                    "notes": "",
                    "data": {
                        "foo": "bar"
                    },
                    "tag_requirements": [
                        {
                            "tag": "workstation",
                            "negate": true
                        }
                    ],
                    "ram": 32,
                    "parents": [],
                    "hidden": false,
                    "priority": 0,
                    "cpus": 1,
                    "state": "queued",
                    "by": 1.0,
                    "time_finished": null
                }

        :statuscode 200: the job was updated
        :statuscode 400: there was something wrong with the request (such as
                            invalid columns being included)
        """
        if isinstance(job_name, STRING_TYPES):
            job = Job.query.filter_by(title=job_name).first()
        else:
            job = Job.query.filter_by(id=job_name).first()

        if not job:
            return jsonify(error="Job not found"), NOT_FOUND

        if "tiles" in g.json:
            return (jsonify(error="Frame tiling cannot be updated after job "
                                  "creation."), BAD_REQUEST)

        old_first_task = Task.query.filter_by(job=job).order_by(
            "frame asc").first()
        old_last_task = Task.query.filter_by(job=job).order_by(
            "frame desc").first()

        if not old_first_task or not old_last_task: # pragma: no cover
            return (jsonify(error="Job does not have any tasks"),
                    INTERNAL_SERVER_ERROR)

        start = old_first_task.frame
        end = old_last_task.frame

        if "start" in g.json or "end" in g.json or "by" in g.json:
            json = loads(request.data.decode(), parse_float=Decimal)
            start = Decimal(json.pop("start", old_first_task.frame))
            end = Decimal(json.pop("end", old_last_task.frame))
            by = Decimal(json.pop("by", job.by))

            try:
                job.alter_frame_range(start, end, by)
            except ValueError as e:
                return (jsonify(
                        error=str(e)), BAD_REQUEST)

        if "parents" in g.json:
            parents = []
            for parent_job_data in g.json["parents"]:
                parent_job = Job.query.filter_by(
                    id=parent_job_data["id"]).first()
                if not parent_job:
                    return (jsonify("Parent job %s not found" %
                                    parent_job_data["id"] ), NOT_FOUND)
                parents.append(parent_job)
            job.parents = parents

        username = g.json.pop("user", None)
        if username:
            user = User.query.filter_by(username=username).first()
            if not user:
                return (jsonify(
                    error="User %s not found" % username), NOT_FOUND)
            job.user = user

        jobqueue_name = g.json.pop("jobqueue", None)
        if jobqueue_name:
            jobqueue = None
            path_elements = jobqueue_name.split("/")
            for element in path_elements:
                jobqueue = JobQueue.query.filter_by(
                    parent=jobqueue, name=element).first()
                if not jobqueue:
                    return (jsonify(error="Jobqueue %s not found" %
                                    jobqueue_name),
                            NOT_FOUND)
            job.queue = jobqueue

        if "time_started" in g.json:
            return (jsonify(error="`time_started` cannot be set manually"),
                    BAD_REQUEST)

        if "time_finished" in g.json:
            return (jsonify(error="`time_finished` cannot be set manually"),
                    BAD_REQUEST)

        if "time_submitted" in g.json:
            return (jsonify(error="`time_submitted` cannot be set manually"),
                    BAD_REQUEST)

        if "jobtype_version_id" in g.json:
            return (jsonify(error=
                           "`jobtype_version_id` cannot be set manually"),
                    BAD_REQUEST)

        if "jobgroup" in g.json:
            return (jsonify(error=
                           "`jobgroup` cannot be set directly, use "
                           " job_group_id."), BAD_REQUEST)

        for name in Job.types().columns:
            if name in g.json:
                type = Job.types().mappings[name]
                value = g.json.pop(name)
                if not isinstance(value, type):
                    return jsonify(error="Column `%s` is of type %r, but we "
                                   "expected %r" % (name,
                                                    type(value),
                                                    type))
                setattr(job, name, value)

        if "software_requirements" in g.json:
            try:
                job.software_requirements = parse_requirements(
                    g.json["software_requirements"])
            except (TypeError, ValueError) as e:
                return jsonify(error=e.args), BAD_REQUEST
            except ObjectNotFound as e:
                return jsonify(error=e.args), NOT_FOUND
            del g.json["software_requirements"]

        tag_requirements = g.json.pop("tag_requirements", None)
        if tag_requirements:
            job.tag_requirements = []
            for entry in tag_requirements:
                tag = Tag.query.filter_by(tag=entry["tag"]).first()
                if not tag:
                    tag = Tag(tag=entry["tag"])
                    db.session.add(tag)
                tag_requirement = JobTagRequirement(job=job, tag=tag)
                if entry["negate"]:
                    tag_requirement.negate = True
                db.session.add(tag_requirement)

        jobtype_version_number = g.json.pop("jobtype_version", None)
        if jobtype_version_number:
            jobtype_version = JobTypeVersion.query.filter_by(
                jobtype=job.jobtype_version.jobtype,
                version=jobtype_version_number).first()
            if not jobtype_version:
                return (jsonify(error="Unknown jobtype version: %s" %
                                      jobtype_version),
                        BAD_REQUEST)
            job.jobtype_version = jobtype_version

        g.json.pop("start", None)
        g.json.pop("end", None)
        if g.json:
            return jsonify(error="Unknown columns: %r" % g.json), BAD_REQUEST

        db.session.add(job)
        db.session.commit()
        job_data = job.to_dict(unpack_relationships=["tags",
                                                     "data",
                                                     "software_requirements",
                                                     "parents",
                                                     "children",
                                                     "tag_requirements"])
        job_data["start"] = start
        job_data["end"] = end
        del job_data["jobtype_version_id"]
        job_data["jobtype"] = job.jobtype_version.jobtype.name
        job_data["jobtype_version"] = job.jobtype_version.version
        job_data["user"] = job.user.username if job.user else None
        del job_data["user_id"]
        job_data["jobqueue"] = job.queue.path if job.queue else None
        del job_data["job_queue_id"]
        job_data["jobgroup"] = job.group.title if job.group else None
        if job.state is None:
            num_assigned_tasks = Task.query.filter(Task.job == job,
                                                   Task.agent != None).count()
            if num_assigned_tasks > 0:
                job_data["state"] = "running"
            else:
                job_data["state"] = "queued"

        logger.info("Job %s has been updated to: %r", job.id, job_data)
        assign_tasks.delay()

        return jsonify(job_data), OK

    def delete(self, job_name):
        """
        A ``DELETE`` to this endpoint will mark the specified job for deletion
        and remove it after stopping and removing all of its tasks.

        .. http:delete:: /api/v1/jobs/[<str:name>|<int:id>] HTTP/1.1

            **Request**

            .. sourcecode:: http

                DELETE /api/v1/jobs/1 HTTP/1.1
                Accept: application/json

            **Response**

            .. sourcecode:: http

                HTTP/1.1 204 NO_CONTENT

        :statuscode 204: the specified job was marked for deletion
        :statuscode 404: the job does not exist
        """
        if isinstance(job_name, STRING_TYPES):
            job = Job.query.filter_by(title=job_name).first()
        else:
            job = Job.query.filter_by(id=job_name).first()

        if not job:
            return jsonify(error="Job not found"), NOT_FOUND

        job.to_be_deleted = True

        child_job_ids = []
        for child_job in job.children:
            child_job.to_be_deleted = True
            child_job_ids.append(child_job.id)
            db.session.add(child_job)

        db.session.add(job)
        db.session.commit()

        for id_ in child_job_ids + [job.id]:
            logger.info("Marking job %s for deletion", id_)
            delete_job(id_)

        return jsonify(None), NO_CONTENT


class JobTasksIndexAPI(MethodView):
    def get(self, job_name):
        """
        A ``GET`` to this endpoint will return a list of all tasks in a job.

        .. http:get:: /api/v1/jobs/[<str:name>|<int:id>]/tasks HTTP/1.1

            **Request**

            .. sourcecode:: http

                GET /api/v1/jobs/Test%20Job%202/tasks/ HTTP/1.1
                Accept: application/json

            **Response**

            .. sourcecode:: http

                HTTP/1.1 200 OK
                Content-Type: application/json

                [
                    {
                        "hidden": false,
                        "id": 3,
                        "attempts": 0,
                        "priority": 0,
                        "time_started": null,
                        "time_submitted": "2014-03-06T15:49:51.892228",
                        "frame": 1.0,
                        "time_finished": null,
                        "job_id": 2,
                        "project_id": null,
                        "state": "queued",
                        "agent_id": null
                    },
                    {
                        "hidden": false,
                        "id": 4,
                        "attempts": 0,
                        "priority": 0,
                        "time_started": null,
                        "time_submitted": "2014-03-06T15:49:51.892925",
                        "frame": 2.0,
                        "time_finished": null,
                        "job_id": 2,
                        "project_id": null,
                        "state": "queued",
                        "agent_id": null
                    }
                ]

        :statuscode 200: no error
        """
        if isinstance(job_name, STRING_TYPES):
            job = Job.query.filter_by(title=job_name).first()
        else:
            job = Job.query.filter_by(id=job_name).first()

        if not job:
            return jsonify(error="Job not found",
                           id=job_name), NOT_FOUND

        tasks_q = Task.query.filter_by(job=job).order_by("frame asc")
        out = []
        for task in tasks_q:
            data = task.to_dict(unpack_relationships=False)
            if task.state == None and task.agent == None:
                data["state"] = "queued"
            elif task.state == None:
                data["state"] = "assigned"
            out.append(data)

        return jsonify(out), OK


class JobSingleTaskAPI(MethodView):
    def post(self, job_name, task_id):
        """
        A ``POST`` to this endpoint will update the specified task with the data
        in the request.  Columns not specified in the request will be left as
        they are.
        The agent will use this endpoint to inform the master of its progress.

        .. http:post:: /api/v1/jobs/[<str:name>|<int:id>]/tasks/<int:task_id> HTTP/1.1

            **Request**

            .. sourcecode:: http

                PUT /api/v1/job/Test%20Job/tasks/1 HTTP/1.1
                Accept: application/json

                {
                    "state": "running"
                }

            **Response**

            .. sourcecode:: http

                HTTP/1.1 200 OK
                Content-Type: application/json

                {
                    "time_finished": null,
                    "agent": null,
                    "attempts": 0,
                    "failures": 0,
                    "frame": 2.0,
                    "agent_id": null,
                    "job": {
                        "id": 1,
                        "title": "Test Job"
                    },
                    "time_started": null,
                    "state": "running",
                    "project_id": null,
                    "id": 2,
                    "time_submitted": "2014-03-06T15:40:58.338904",
                    "project": null,
                    "parents": [],
                    "job_id": 1,
                    "hidden": false,
                    "children": [],
                    "priority": 0
                }

        :statuscode 200: the task was updated
        :statuscode 400: there was something wrong with the request (such as
                            invalid columns being included)
        """
        task_query = Task.query.filter_by(id=task_id)
        if isinstance(job_name, STRING_TYPES):
            task_query.filter(Task.job.has(Job.title == job_name))
        else:
            task_query.filter(Task.job.has(Job.id == job_name))
        task = task_query.first()

        if not task:
            return jsonify(error="Task not found"), NOT_FOUND

        if "time_started" in g.json and g.json["time_started"] != "now":
            return (jsonify(error="`time_started` cannot be set manually"),
                    BAD_REQUEST)
        elif "time_started" in g.json and g.json["time_started"] == "now":
            g.json["time_started"] = datetime.utcnow()

        if "time_finished" in g.json:
            return (jsonify(error="`time_finished` cannot be set manually"),
                    BAD_REQUEST)

        if "time_submitted" in g.json:
            return (jsonify(error="`time_submitted` cannot be set manually"),
                    BAD_REQUEST)

        if "job_id" in g.json:
            return jsonify(error="`job_id` cannot be changed"), BAD_REQUEST

        if "frame" in g.json:
            return jsonify(error="`frame` cannot be changed"), BAD_REQUEST

        if (("state" in g.json or "progress" in g.json) and
            request.headers.get("User-Agent", "") == "PyFarm/1.0 (agent)" and
            (task.agent is None or
             request.remote_addr != task.agent.remote_ip)):
            logger.error("Agent with IP address %s tried to set state or "
                         "progress for task %s. IP address for assigned agent "
                         "is %s. Request rejected.",
                         request.remote_addr, task.id,
                         (task.agent.remote_ip if task.agent else "(n/a)"))
            return jsonify(error="`state` and `progress` can only be changed "
                                 "by the agent owning this task"), BAD_REQUEST

        if (task.state == _WorkState.DONE and
            "progress" in g.json and
            g.json["progress"] != 1.0):
            return jsonify(error="Cannot set progress: task is already in "
                                 "state `done`"), BAD_REQUEST

        new_state = g.json.pop("state", None)
        agent = task.agent
        state_transition = False
        if new_state is not None and new_state != task.state:
            logger.info("Task %s of job %s: state transition \"%s\" -> \"%s\"",
                        task_id, task.job.title, task.state, new_state)
            state_transition = True
            if new_state != "queued":
                task.state = new_state
            else:
                task.state = None

        # Iterate over all keys in the request
        for key in list(g.json):
            if key in TASK_MODEL_MAPPINGS:
                value = g.json.pop(key)
                expected_types = TASK_MODEL_MAPPINGS[key]

                # incorrect type for `value`
                if not isinstance(value, (expected_types, type(None))):
                    return (jsonify(
                        error="Column %r is of type %r but we expected "
                              "type(s) %r" % (key, type(value),
                                              expected_types)), BAD_REQUEST)

                # correct type for `value`
                setattr(task, key, value)

        if g.json:
            return (jsonify(error="Unknown columns in request: %r" % g.json),
                    BAD_REQUEST)

        db.session.add(task)
        db.session.commit()

        task_data = task.to_dict(unpack_relationships=("job", "agent",
                                                       "children", "parents",
                                                       "project"))
        if task.state is None and task.agent is None:
            task_data["state"] = "queued"
        elif task.state is None:
            task_data["state"] = "assigned"
        logger.info("Task %s of job %s has been updated, new data: %r",
                    task_id, task.job.title, task_data)

        if agent:
            task_count = Task.query.filter(
                Task.agent == agent,
                or_(Task.state == None,
                    Task.state == WorkState.RUNNING)).\
                        order_by(Task.job_id, Task.frame).count()
            if task_count == 0:
                assign_tasks_to_agent.delay(agent.id)

        # This needs to be done after the transaction in which the task state
        # was set has committed, so that the new transaction will see the results
        # of other threads that were running concurrently but finished earlier.
        if task.job and state_transition:
            old_state = task.job.state
            task.job.update_state()
            db.session.commit()
            if task.job.state != old_state and task.job.state == WorkState.DONE:
                assign_tasks.delay()

        if config.get("enable_statistics") and task.job and state_transition:
            task_event_count = TaskEventCount(
                job_queue_id=task.job.job_queue_id)
            if new_state == "queued":
                task_event_count.num_restarted = 1
            elif new_state == "running":
                task_event_count.num_started = 1
            elif new_state == "done":
                task_event_count.num_done = 1
            elif new_state == "failed":
                task_event_count.num_failed = 1
            task_event_count.time_start = datetime.utcnow()
            task_event_count.time_end = datetime.utcnow()
            db.session.add(task_event_count)
            db.session.commit()

        return jsonify(task_data), OK

    def get(self, job_name, task_id):
        """
        A ``GET`` to this endpoint will return the requested task

        .. http:get:: /api/v1/jobs/[<str:name>|<int:id>]/tasks/<int:task_id> HTTP/1.1

            **Request**

            .. sourcecode:: http

                GET /api/v1/jobs/Test%20Job%202/tasks/1 HTTP/1.1
                Accept: application/json

            **Response**

            .. sourcecode:: http

                HTTP/1.1 200 OK
                Content-Type: application/json

                {
                    "time_finished": null,
                    "agent": null,
                    "attempts": 0,
                    "frame": 2.0,
                    "agent_id": null,
                    "job": {
                        "id": 1,
                        "title": "Test Job"
                    },
                    "time_started": null,
                    "state": "running",
                    "project_id": null,
                    "id": 2,
                    "time_submitted": "2014-03-06T15:40:58.338904",
                    "project": null,
                    "parents": [],
                    "job_id": 1,
                    "hidden": false,
                    "children": [],
                    "priority": 0
                }

        :statuscode 200: no error
        """
        task_query = Task.query.filter_by(id=task_id)
        if isinstance(job_name, STRING_TYPES):
            task_query.filter(Task.job.has(Job.title == job_name))
        else:
            task_query.filter(Task.job.has(Job.id == job_name))
        task = task_query.first()

        if not task:
            return jsonify(error="Task not found"), NOT_FOUND

        task_data = task.to_dict(unpack_relationships=("job", "agent",
                                                       "children", "parents",
                                                       "project"))
        if task.state is None and task.agent is None:
            task_data["state"] = "queued"
        elif task.state is None:
            task_data["state"] = "assigned"

        return jsonify(task_data), OK


class TaskFailedOnAgentsIndexAPI(MethodView):
    def get(self, job_id, task_id):
        """
        A ``GET`` to this endpoint will return a list of all agents that failed
        to execute this task

        .. http:get:: /api/v1/jobs/<int:job_id>/tasks/<int:task_id>/failed_on_agents/ HTTP/1.1

            **Request**

            .. sourcecode:: http

                GET /api/v1/jobs/12/tasks/1234/failed_on_agents/ HTTP/1.1
                Accept: application/json

            **Response**

            .. sourcecode:: http

                HTTP/1.1 200 OK
                Content-Type: application/json

                [
                    {
                        "id": "02f08241-c556-4355-9e5e-33243d8c4577",
                        "hostname": "agent1"
                    }
                ]

        :statuscode 200: no error
        :statuscode 404: job or task not found
        """
        task_query = Task.query.filter_by(id=task_id)
        task_query.filter(Task.job.has(Job.id == job_id))
        task = task_query.first()

        if not task:
            return jsonify(error="Task not found"), NOT_FOUND

        out = []
        for agent in task.failed_in_agents:
            out.append({"hostname": agent.hostname,
                        "id": agent.id})

        return jsonify(out), OK

    def post(self, job_id, task_id):
        """
        A ``POST`` to this endpoint will add the specified agent to the list
        of agents that failed to execute this task

        .. http:post:: /api/v1/jobs/<int:id>/tasks/<int:task_id>/failed_on_agents/ HTTP/1.1

            **Request**

            .. sourcecode:: http

                POST /api/v1/jobs/12/tasks/1234/failed_on_agents/ HTTP/1.1
                Accept: application/json
                Content-Type: application/json

                {
                    "id": "02f08241-c556-4355-9e5e-33243d8c4577"
                }

            **Response**

            .. sourcecode:: http

                HTTP/1.1 201 CREATED
                Content-Type: application/json

                {
                    "id": "02f08241-c556-4355-9e5e-33243d8c4577",
                    "hostname": "agent1"
                }

        :statuscode 201: a new entry was created
        :statuscode 400: there was something wrong with the request (such as
                         invalid columns being included)
        :statuscode 404: the job, task or agent specified does not exist
        """
        task_query = Task.query.filter_by(id=task_id)
        task_query.filter(Task.job.has(Job.id == job_id))
        task = task_query.first()

        if not task:
            return jsonify(error="Task not found"), NOT_FOUND

        agent_id = g.json.get("id")
        agent = Agent.query.filter_by(id=agent_id).first()
        if not agent:
            return jsonify(error="Agent not found"), NOT_FOUND

        if agent not in task.failed_in_agents:
            task.failed_in_agents.append(agent)

        db.session.add(task)
        db.session.commit()

        return jsonify({"id": agent_id, "hostname": agent.hostname}), CREATED


class SingleTaskOnAgentFailureAPI(MethodView):
    def delete(self, job_id, task_id, agent_id):
        """
        A ``DELETE`` to this endpoint will remove the specified agent from the
        list of agents that failed to execute this task

        .. http:delete:: /api/v1/jobs/<int:id>/tasks/<int:task_id>/failed_on_agents/<str:agent_id> HTTP/1.1

            **Request**

            .. sourcecode:: http

                DELETE /api/v1/jobs/12/tasks/1234/failed_on_agents/02f08241-c556-4355-9e5e-33243d8c4577 HTTP/1.1

            **Response**

            .. sourcecode:: http

                HTTP/1.1 204 NO_CONTENT

        :statuscode 204: the given agent was removed from the list of agents
                         that failed to execute this task
        :statuscode 404: the job, task or agent does not exist
        """
        task_query = Task.query.filter_by(id=task_id)
        task_query.filter(Task.job.has(Job.id == job_id))
        task = task_query.first()

        if not task:
            return jsonify(error="Task not found"), NOT_FOUND

        agent = Agent.query.filter_by(id=agent_id).first()
        if not agent:
            return jsonify(error="Agent not found"), NOT_FOUND

        if agent in task.failed_in_agents:
            task.failed_in_agents.remove(agent)

        return jsonify(), NO_CONTENT


class JobNotifiedUsersIndexAPI(MethodView):
    def get(self, job_name):
        """
        A ``GET`` to this endpoint will return a list of all users to be notified
        on events in this job.

        .. http:get:: /api/v1/jobs/[<str:name>|<int:id>]/notified_users/ HTTP/1.1

            **Request**

            .. sourcecode:: http

                GET /api/v1/jobs/Test%20Job%202/notified_users/ HTTP/1.1
                Accept: application/json

            **Response**

            .. sourcecode:: http

                HTTP/1.1 200 OK
                Content-Type: application/json

                [
                    {
                        "id": 1,
                        "username": "testuser",
                        "email": "testuser@localhost"
                    }
                ]

        :statuscode 200: no error
        :statuscode 404: job not found
        """
        if isinstance(job_name, STRING_TYPES):
            job = Job.query.filter_by(title=job_name).first()
        else:
            job = Job.query.filter_by(id=job_name).first()

        if not job:
            return jsonify(error="Job not found"), NOT_FOUND

        out = []
        for notified_user in job.notified_users:
            out.append({
                "id": notified_user.user_id,
                "username": notified_user.user.username,
                "email": notified_user.user.email,
                "on_success": notified_user.on_success,
                "on_failure": notified_user.on_failure,
                "on_deletion": notified_user.on_deletion})

        return jsonify(out), OK

    def post(self, job_name):
        """
        A ``POST`` to this endpoint will add the specified user to the list of
        notified users for this job.

        .. http:post:: /api/v1/jobs/[<str:name>|<int:id>]/notified_users/ HTTP/1.1

            **Request**

            .. sourcecode:: http

                POST /api/v1/jobs/Test%20Job/notified_users/ HTTP/1.1
                Accept: application/json

                {
                    "username": "testuser"
                    "on_success": true,
                    "on_failure": true,
                    "on_deletion": false
                }

            **Response**

            .. sourcecode:: http

                HTTP/1.1 201 CREATED
                Content-Type: application/json

                {
                    "id": 1
                    "username": "testuser"
                    "email": "testuser@example.com"
                }

        :statuscode 201: a new notified user entry was created
        :statuscode 400: there was something wrong with the request (such as
                         invalid columns being included)
        :statuscode 404: the job or the specified user does not exist
        """
        if isinstance(job_name, STRING_TYPES):
            job = Job.query.filter_by(title=job_name).first()
        else:
            job = Job.query.filter_by(id=job_name).first()

        if not job:
            return jsonify(error="Job not found"), NOT_FOUND

        if "username" not in g.json:
            return jsonify(error="No username specified"), BAD_REQUEST

        username = g.json.pop("username")
        user = User.query.filter_by(username=username).first()
        if not user:
            return jsonify("User %s not found" % username), NOT_FOUND

        notified_user = JobNotifiedUser(job=job, user=user)
        if "on_success" in g.json:
            notified_user.on_success = g.json.pop("on_success")
        if "on_failure" in g.json:
            notified_user.on_failure = g.json.pop("on_failure")
        if "on_deletion" in g.json:
            notified_user.on_deletion = g.json.pop("on_deletion")

        if g.json:
            return jsonify(error="Unknown fields in request"), BAD_REQUEST

        db.session.add(notified_user)
        db.session.commit()

        logger.info("Added user %s (id %s) to notified users for job %s (%s)",
                    user.username,
                    user.id,
                    job.title,
                    job.id)

        return (jsonify({
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "on_success": notified_user.on_success,
                    "on_failure": notified_user.on_failure,
                    "on_deletion": notified_user.on_deletion}),
                CREATED)


class JobSingleNotifiedUserAPI(MethodView):
    def delete(self, job_name, username):
        """
        A ``DELETE`` to this endpoint will remove the specified user from the
        list of notified users for this job.

        .. http:delete:: /api/v1/jobs/[<str:name>|<int:id>]/notified_users/<str:username> HTTP/1.1

            **Request**

            .. sourcecode:: http

                DELETE /api/v1/jobs/Test%20Job/notified_users/testuser HTTP/1.1
                Accept: application/json

            **Response**

            .. sourcecode:: http

                HTTP/1.1 204 NO_CONTENT

        :statuscode 204: the notified user was removed from this job or wasn't
                         in the list in the first place
        :statuscode 404: the job or the specified user does not exist
        """
        if isinstance(job_name, STRING_TYPES):
            job = Job.query.filter_by(title=job_name).first()
        else:
            job = Job.query.filter_by(id=job_name).first()

        if not job:
            return jsonify(error="Job not found"), NOT_FOUND

        user = User.query.filter_by(username=username).first()
        if not user:
            return jsonify(error="User %s not found" % username), NOT_FOUND

        notified_user = JobNotifiedUser.query.filter_by(
            job=job, user=user).first()

        if not notified_user:
            return jsonify(), NO_CONTENT

        db.session.delete(notified_user)
        db.session.commit()

        logger.info("Removed user %s (id %s) from notified users for "
                    "job %s (%s)", user.username, user.id, job.title, job.id)

        return jsonify(), NO_CONTENT
