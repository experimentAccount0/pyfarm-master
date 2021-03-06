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

import time
import uuid
from datetime import datetime, timedelta

# test class must be loaded first
from pyfarm.master.testutil import BaseTestCase
BaseTestCase.build_environment()

from pyfarm.master.application import db, login_serializer
from pyfarm.models.user import User, Role


class UserTest(BaseTestCase):
    def test_create_user(self):
        username = uuid.uuid4().hex
        password = uuid.uuid4().hex
        user_id = User.create(username, password).id
        db.session.remove()
        user = User.query.filter_by(id=user_id).first()
        self.assertIsNotNone(user)
        self.assertEqual(user.username, username)

    def test_user_auth_token(self):
        username = uuid.uuid4().hex
        password = uuid.uuid4().hex
        user = User.create(username, password)
        self.assertEqual(
            user.get_auth_token(),
            login_serializer.dumps([str(user.id), user.password]))

    def test_get_id(self):
        username = uuid.uuid4().hex
        password = uuid.uuid4().hex
        user = User.create(username, password)
        self.assertEqual(user.get_id(), user.id)

    def test_hash_password(self):
        username = uuid.uuid4().hex
        password = uuid.uuid4().hex
        user = User.create(username, password)
        self.assertEqual(user.password, User.hash_password(password))

    def test_check_password(self):
        username = uuid.uuid4().hex
        password = uuid.uuid4().hex
        user = User.create(username, password)
        self.assertTrue(user.check_password(password))
        self.assertFalse(user.check_password(""))

        with self.assertRaises(AssertionError):
            user.check_password(None)

    def test_get(self):
        username = uuid.uuid4().hex
        password = uuid.uuid4().hex
        user = User.create(username, password)
        user_by_id = User.get(user.id)
        self.assertEqual(user_by_id.id, user.id)
        user_by_name = User.get(user.username)
        self.assertEqual(user_by_name.id, user.id)

        with self.assertRaises(TypeError):
            User.get(None)

    def test_is_active(self):
        username = uuid.uuid4().hex
        password = uuid.uuid4().hex
        user = User.create(username, password)
        self.assertTrue(user.is_active())
        user.active = False
        self.assertFalse(user.is_active())
        user.active = True
        self.assertTrue(user.is_active())
        user.expiration = datetime.utcnow() + timedelta(seconds=.5)
        time.sleep(1.5)
        self.assertFalse(user.is_active())
        user.expiration = datetime.utcnow() + timedelta(days=1)
        self.assertTrue(user.is_active())

    def test_roles(self):
        username = uuid.uuid4().hex
        password = uuid.uuid4().hex
        user = User.create(username, password)
        self.assertTrue(user.has_roles())
        username = uuid.uuid4().hex
        password = uuid.uuid4().hex
        user = User(username=username, password=password)
        role_names = map(lambda i: uuid.uuid4().hex, range(3))
        roles = []
        for role_name in role_names:
            role = Role.create(role_name)
            user.roles.append(role)
            roles.append(role)

        db.session.add(user)
        db.session.commit()
        self.assertTrue(
            user.has_roles(allowed=[roles[0].name, roles[1].name]))
        self.assertTrue(
            user.has_roles(allowed=[roles[0].name, "foo"]))
        self.assertTrue(
            user.has_roles(allowed=["foo", roles[0].name]))
        self.assertFalse(
            user.has_roles(allowed=["foo"]))
        self.assertTrue(
            user.has_roles(required=[roles[0].name, roles[1].name]))
        self.assertFalse(
            user.has_roles(required=[roles[0].name, roles[1].name, "foo"]))


class RoleTest(BaseTestCase):
    def test_create(self):
        rolename = uuid.uuid4().hex
        role_id = Role.create(rolename).id
        db.session.remove()
        role = Role.query.filter_by(id=role_id).first()
        self.assertEqual(role.name, rolename)
        self.assertIs(Role.create(role), role)
        new_role = Role.create(rolename)
        self.assertEqual(new_role.id, role.id)

    def test_active(self):
        rolename = uuid.uuid4().hex
        role = Role.create(rolename)
        role.active = False
        self.assertFalse(role.is_active())
        role.active = True
        self.assertTrue(role.is_active())
        role.expiration = datetime.utcnow() - timedelta(days=1)
        self.assertFalse(role.is_active())
        role.expiration = datetime.utcnow() + timedelta(days=1)
        self.assertTrue(role.is_active())
        role.expiration = datetime.utcnow() + timedelta(seconds=.5)
        self.assertTrue(role.is_active())
        time.sleep(1)
        self.assertFalse(role.is_active())
