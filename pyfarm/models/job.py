# No shebang line, this module is meant to be imported
#
# Copyright 2013 Oliver Palmer
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
Job Models
==========

Models and interface classes related to jobs.

.. include:: ../include/references.rst
"""

try:
    import pwd
except ImportError:  # pragma: no cover
    pwd = None

try:
    import json
except ImportError:  # pragma: no cover
    import simplejson as json

from functools import partial
from textwrap import dedent

from sqlalchemy import event
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import validates
from sqlalchemy.schema import UniqueConstraint

from pyfarm.core.config import read_env, read_env_int
from pyfarm.core.enums import WorkState
from pyfarm.master.application import db
from pyfarm.models.core.functions import work_columns, repr_enum
from pyfarm.models.core.types import id_column, JSONDict, JSONList, IDTypeWork
from pyfarm.models.core.cfg import (
    TABLE_JOB, TABLE_JOB_TAG, TABLE_JOB_SOFTWARE,
    MAX_COMMAND_LENGTH, MAX_TAG_LENGTH, MAX_USERNAME_LENGTH,
    TABLE_JOB_DEPENDENCIES, TABLE_PROJECT)
from pyfarm.models.core.mixins import (
    WorkValidationMixin, StateChangedMixin, ReprMixin)
from pyfarm.models.jobtype import JobType  # required for a relationship


class JobTag(db.Model):
    """
    Model which provides tagging for :class:`.Job` objects

    .. note::
        This table enforces two forms of uniqueness.  The :attr:`id` column
        must be unique and the combination of these columns must also be
        unique to limit the frequency of duplicate data:

            * :attr:`job_id`
            * :attr:`tag`

    .. autoattribute:: job_id
    """
    __tablename__ = TABLE_JOB_TAG
    __table_args__ = (
        UniqueConstraint("job_id", "tag"), )

    id = id_column()
    job_id = db.Column(IDTypeWork, db.ForeignKey("%s.id" % TABLE_JOB),
                       nullable=False,
                       doc=dedent("""
                       Foreign key which stores :attr:`Job.id`"""))

    tag = db.Column(db.String(MAX_TAG_LENGTH), nullable=False)


class JobSoftware(db.Model):
    """
    Model which allows specific software to be associated with a
    :class:`.Job` object.

    .. note::
        This table enforces two forms of uniqueness.  The :attr:`id` column
        must be unique and the combination of these columns must also be
        unique to limit the frequency of duplicate data:

            * :attr:`job_id`
            * :attr:`software`
            * :attr:`version`

    .. autoattribute:: job_id
    """
    __tablename__ = TABLE_JOB_SOFTWARE
    __table_args__ = (
        UniqueConstraint("job_id", "software", "version"), )

    id = id_column()
    job_id = db.Column(IDTypeWork, db.ForeignKey("%s.id" % TABLE_JOB),
                       nullable=False,
                       doc=dedent("""
                       The foreign key which stores :attr:`Job.id`"""))
    software = db.Column(db.String(MAX_TAG_LENGTH), nullable=False,
                         doc=dedent("""
                         The name of the software required to run a job"""))
    version = db.Column(db.String(MAX_TAG_LENGTH),
                        default="any", nullable=False,
                        doc=dedent("""
                        The version of software required to run the job.  This
                        value does not follow any special formatting rules
                        because the format depends on the 3rd party."""))


JobDependencies = db.Table(
    TABLE_JOB_DEPENDENCIES, db.metadata,
    db.Column("parentid", IDTypeWork,
              db.ForeignKey("%s.id" % TABLE_JOB), primary_key=True),
    db.Column("childid", IDTypeWork,
              db.ForeignKey("%s.id" % TABLE_JOB), primary_key=True))


class Job(db.Model, WorkValidationMixin, StateChangedMixin, ReprMixin):
    """
    Defines the attributes and environment for a job.  Individual commands
    are kept track of by |Task|
    """
    __tablename__ = TABLE_JOB
    STATE_ENUM = WorkState
    REPR_COLUMNS = ("id", "state", "project")
    REPR_CONVERT_COLUMN = {
        "state": partial(repr_enum, enum=STATE_ENUM)}
    MIN_CPUS = read_env_int("PYFARM_QUEUE_MIN_CPUS", 1)
    MAX_CPUS = read_env_int("PYFARM_QUEUE_MAX_CPUS", 256)
    MIN_RAM = read_env_int("PYFARM_QUEUE_MIN_RAM", 16)
    MAX_RAM = read_env_int("PYFARM_QUEUE_MAX_RAM", 262144)
    SPECIAL_RAM = read_env("PYFARM_AGENT_SPECIAL_RAM", [0], eval_literal=True)
    SPECIAL_CPUS = read_env("PYFARM_AGENT_SPECIAL_CPUS", [0], eval_literal=True)

    # quick check of the configured data
    assert MIN_CPUS >= 1, "$PYFARM_QUEUE_MIN_CPUS must be > 0"
    assert MAX_CPUS >= 1, "$PYFARM_QUEUE_MAX_CPUS must be > 0"
    assert MAX_CPUS >= MIN_CPUS, "MIN_CPUS must be <= MAX_CPUS"
    assert MIN_RAM >= 1, "$PYFARM_QUEUE_MIN_RAM must be > 0"
    assert MAX_RAM >= 1, "$PYFARM_QUEUE_MAX_RAM must be > 0"
    assert MAX_RAM >= MIN_RAM, "MIN_RAM must be <= MAX_RAM"


    # shared work columns
    id, state, priority, time_submitted, time_started, time_finished = \
        work_columns(STATE_ENUM.QUEUED, "job.priority")
    project_id = db.Column(db.Integer, db.ForeignKey("%s.id" % TABLE_PROJECT),
                           doc="stores the project id")
    user = db.Column(db.String(MAX_USERNAME_LENGTH),
                     doc=dedent("""
                     The user this job should execute as.  The agent
                     process will have to be running as root on platforms
                     that support setting the user id.

                     .. note::
                        The length of this field is limited by the
                        configuration value `job.max_username_length`

                     .. warning::
                        this may not behave as expected on all platforms
                        (windows in particular)"""))
    notes = db.Column(db.Text, default="",
                      doc=dedent("""
                      Notes that are provided on submission or added after
                      the fact. This column is only provided for human
                      consumption is not scanned, index, or used when
                      searching"""))

    # task data
    cmd = db.Column(db.String(MAX_COMMAND_LENGTH),
                    doc=dedent("""
                    The platform independent command to run. Each agent will
                    resolve this value for itself when the task begins so a
                    command like `ping` will work on any platform it's
                    assigned to.  The full commend could be provided here,
                    but then the job must be tagged using
                    :class:`.JobSoftware` to limit which agent(s) it will
                    run on."""))
    start = db.Column(db.Float,
                      doc=dedent("""
                      The first frame of the job to run.  This value may
                      be a float so subframes can be processed."""))
    end = db.Column(db.Float,
                      doc=dedent("""
                      The last frame of the job to run.  This value may
                      be a float so subframes can be processed."""))
    by = db.Column(db.Float, default=1,
                   doc=dedent("""
                   The number of frames to count by between `start` and
                   `end`.  This column may also sometimes be referred to
                   as 'step' by other software."""))
    batch = db.Column(db.Integer,
                      default=read_env_int("PYFARM_QUEUE_DEFAULT_BATCH", 1),
                      doc=dedent("""
                      Number of tasks to run on a single agent at once.
                      Depending on the capabilities of the software being run
                      this will either cause a single process to execute on
                      the agent or multiple processes on after the other.

                      **configured by**: `job.batch`"""))
    requeue = db.Column(db.Integer,
                        default=read_env_int("PYFARM_QUEUE_DEFAULT_REQUEUE", 3),
                        doc=dedent("""
                        Number of times to requeue failed tasks

                        .. csv-table:: **Special Values**
                            :header: Value, Result
                            :widths: 10, 50

                            0, never requeue failed tasks
                            -1, requeue failed tasks indefinitely

                        **configured by**: `job.requeue`"""))
    cpus = db.Column(db.Integer,
                     default=read_env_int("PYFARM_QUEUE_DEFAULT_CPUS", 1),
                     doc=dedent("""
                     Number of cpus or threads each task should consume on
                     each agent.  Depending on the job type being executed
                     this may result in additional cpu consumption, longer
                     wait times in the queue (2 cpus means 2 'fewer' cpus on
                     an agent), or all of the above.

                     .. csv-table:: **Special Values**
                        :header: Value, Result
                        :widths: 10, 50

                        0, minimum number of cpu resources not required
                        -1, agent cpu is exclusive for a task from this job

                     **configured by**: `job.cpus`"""))
    ram = db.Column(db.Integer,
                    default=read_env_int("PYFARM_QUEUE_DEFAULT_RAM", 32),
                    doc=dedent("""
                    Amount of ram a task from this job will require to be
                    free in order to run.  A task exceeding this value will
                    not result in any special behavior.

                    .. csv-table:: **Special Values**
                        :header: Value, Result
                        :widths: 10, 50

                        0, minimum amount of free ram not required
                        -1, agent ram is exclusive for a task from this job

                    **configured by**: `job.ram`"""))
    ram_warning = db.Column(db.Integer, default=-1,
                            doc=dedent("""
                            Amount of ram used by a task before a warning
                            raised.  A task exceeding this value will not
                            cause any work stopping behavior.

                            .. csv-table:: **Special Values**
                                :header: Value, Result
                                :widths: 10, 50

                                -1, not set"""))
    ram_max = db.Column(db.Integer, default=-1,
                        doc=dedent("""
                        Maximum amount of ram a task is allowed to consume on
                        an agent.

                        .. warning::
                            The task will be **terminated** if the ram in use
                            by the process exceeds this value.

                        .. csv-table:: **Special Values**
                            :header: Value, Result
                            :widths: 10, 50

                            -1, not set
                        """))
    attempts = db.Column(db.Integer,
                         doc=dedent("""
                         The number attempts which have been made on this
                         task. This value is auto incremented when
                         :attr:`state` changes to a value synonyms with a
                         running state."""))
    hidden = db.Column(db.Boolean, default=False, nullable=False,
                       doc=dedent("""
                       If True, keep the job hidden from the queue and web
                       ui.  This is typically set to True if you either want
                       to save a job for later viewing or if the jobs data
                       is being populated in a deferred manner."""))
    environ = db.Column(JSONDict,
                        doc=dedent("""
                        Dictionary containing information about the environment
                        in which the job will execute.

                        .. note::
                            Changes made directly to this object are **not**
                            applied to the session."""))
    args = db.Column(JSONList,
                     doc=dedent("""
                     List containing the command line arguments.

                     .. note::
                        Changes made directly to this object are **not**
                        applied to the session."""))
    data = db.Column(JSONDict,
                     doc=dedent("""
                     Json blob containing additional data for a job

                     .. note::
                        Changes made directly to this object are **not**
                        applied to the session."""))

    project = db.relationship("Project",
                              backref=db.backref("jobs", lazy="dynamic"),
                              doc=dedent("""
                              relationship attribute which retrieves the
                              associated project for the job"""))

    # self-referential many-to-many relationship
    parents = db.relationship("Job",
                              secondary=JobDependencies,
                              primaryjoin=id==JobDependencies.c.parentid,
                              secondaryjoin=id==JobDependencies.c.childid,
                              backref="children")

    tasks_done = db.relationship("Task", lazy="dynamic",
        primaryjoin="(Task.state == %s) & "
                    "(Task.job_id == Job.id)" % STATE_ENUM.DONE,
        doc=dedent("""
        Relationship between this job and any |Task| objects which are
        done."""))

    tasks_failed = db.relationship("Task", lazy="dynamic",
        primaryjoin="(Task.state == %s) & "
                    "(Task.job_id == Job.id)" % STATE_ENUM.FAILED,
        doc=dedent("""
        Relationship between this job and any |Task| objects which have
        failed."""))

    tasks_queued = db.relationship("Task", lazy="dynamic",
        primaryjoin="(Task.state == %s) & "
                    "(Task.job_id == Job.id)" % STATE_ENUM.QUEUED,
        doc=dedent("""
        Relationship between this job and any |Task| objects which
        are queued."""))

    # resource relationships
    tags = db.relationship("JobTag", backref="job", lazy="dynamic",
                           doc=dedent("""
                           Relationship between this job and
                           :class:`.JobTag` objects"""))
    software = db.relationship("JobSoftware", backref="job",
                               lazy="dynamic",
                               doc=dedent("""
                               Relationship between this job and
                               :class:`.JobSoftware` objects"""))
    jobtype = db.relationship("JobType", backref="job", lazy="dynamic",
                              doc=dedent("""
                              Relationship between this job and
                              :class:`.JobType` objects."""))

    @validates("ram", "cpus")
    def validate_resource(self, key, value):
        """
        Validation that ensures that the value provided for either
        :attr:`.ram` or :attr:`.cpus` is a valid value with a given range
        """
        key_upper = key.upper()
        special = getattr(self, "SPECIAL_%s" % key_upper)

        if value is None or value in special:
            return value

        min_value = getattr(self, "MIN_%s" % key_upper)
        max_value = getattr(self, "MAX_%s" % key_upper)

        # quick sanity check of the incoming config
        assert isinstance(min_value, int), "db.min_%s must be an integer" % key
        assert isinstance(max_value, int), "db.max_%s must be an integer" % key
        assert min_value >= 1, "db.min_%s must be > 0" % key
        assert max_value >= 1, "db.max_%s must be > 0" % key

        # check the provided input
        if min_value > value or value > max_value:
            msg = "value for `%s` must be between " % key
            msg += "%s and %s" % (min_value, max_value)
            raise ValueError(msg)

        return value


event.listen(Job.state, "set", Job.stateChangedEvent)


def get_job_id():
    """
    Creates a new job without any data and inserts it into the
    database.

    :exception ValueError:
        raised if the session is dirty before trying to create
        and commit a job model
    """
    # TODO: we should create a new session instead
    if db.session.dirty:
        raise ValueError("session is dirty, cannot proceed")

    model = Job()
    model.state = WorkState.ALLOC
    model.hidden = True

    try:
        db.session.add(model)
        db.session.commit()

    except DatabaseError:
        db.session.rollback()
        raise

    else:
        return model.id