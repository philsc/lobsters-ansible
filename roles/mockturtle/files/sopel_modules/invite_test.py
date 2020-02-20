#!/usr/bin/env python3

import os
import unittest
from unittest import mock
import re
import logging
import sys
from tempfile import TemporaryDirectory

import sopel.bot
import sopel.config
import sopel.tests.factories
import sopel.trigger
from sopel.db import SopelDB
from sopel.loader import is_triggerable, clean_module
from sopel.test_tools import MockSopel, MockSopelWrapper

import invite


TEST_CONFIG="""
[core]
owner = Bar
nick = Sopel
enable = coretasks
"""

class MockConfig(sopel.config.Config):
    def __init__(self):
        self._tmpdir = TemporaryDirectory()
        self._tmpfile = os.path.join(self._tmpdir.name, "test.cfg")
        with open(self._tmpfile, "w") as file:
            file.write(TEST_CONFIG)
        super().__init__(self._tmpfile)

    def cleanup(self):
        self._tmpdir.cleanup()


class MockTime():
    """Mocks time for unit testing purposes.

    The purpose of this class is to simulate time in such a way that is
    predictable and that passage of time is instantenous. The clock starts at
    zero. Sleeps will return immediately and move the clock forward
    appropriately.

    Use this class to mock time in a specific module along the lines of:

        import module

        def MyTest(unittest.TestCase):
            def setUp(self):
                self.clock = TimeMock()

            @mock.patch("module.time.sleep")
            @mock.patch("module.time.time")
            def test_module(self, mock_time, mock_sleep):
                mock_time.side_effect = self.clock.time
                mock_sleep.side_effect = self.clock.sleep
                module.function_to_be_tested()
    """

    def __init__(self):
        self._fake_time = 0

    def sleep(self, duration):
        '''Fakes the time.sleep() function.'''
        self._fake_time += duration

    def time(self):
        '''Fakes the time.time() function.'''
        return self._fake_time


class TestInvite(unittest.TestCase):
    """Test case docstring."""

    def setUp(self):
        self.time = MockTime()
        self.config = MockConfig()
        botfactory = sopel.tests.factories.BotFactory()
        self.bot = botfactory.preloaded(self.config)

        self.callables, self.jobs, self.shutdowns, self.urls = clean_module(invite, self.bot.config)

    def tearDown(self):
        self.config.cleanup()

    def _match(self, _):
        # TODO(phil): I think we're simulating a regex search result here. Not
        # sure yet.
        return None

    def trigger_event(self, event_name):
        full_message = ':hostmask %s #channel user :Some text' % event_name
        pretrigger = sopel.trigger.PreTrigger(self.bot.nick, full_message)
        trigger = sopel.trigger.Trigger(self.bot.config, pretrigger, self._match)
        wrapper = sopel.bot.SopelWrapper(self.bot, trigger)
        num_events = 0

        for function in self.callables:
            if not hasattr(function, "event"):
                continue

            for event in function.event:
                # TODO(phil): Can we make this more generic?
                if event == event_name:
                    function(wrapper, trigger)
                    num_events += 1

        return (num_events, b"".join(wrapper.backend.message_sent))

    def trigger_rule(self, message):
        full_message = ':hostmask PRIVMSG #channel :%s' % message
        pretrigger = sopel.trigger.PreTrigger(self.bot.nick, full_message)
        trigger = sopel.trigger.Trigger(self.bot.config, pretrigger, self._match)
        wrapper = sopel.bot.SopelWrapper(self.bot, trigger)
        num_rules = 0

        for function in self.callables:
            if not hasattr(function, "rule"):
                continue

            for event in function.rule:
                match = re.match(event, message)
                if match:
                    function(wrapper, trigger)
                    num_rules += 1

        return (num_rules, b"".join(wrapper.backend.message_sent))


    def test_basics(self):
        self.assertTrue(is_triggerable(invite.invite))
        self.assertTrue(is_triggerable(invite.note))


    @mock.patch("invite.time.sleep")
    @mock.patch("invite.time.time")
    def test_successful_hint(self, mock_time, mock_sleep):
        mock_time.side_effect = self.time.time
        mock_sleep.side_effect = self.time.sleep

        (num_events, output) = self.trigger_event("JOIN")
        self.assertEqual(num_events, 1)
        self.assertEqual(output, b"")

        (num_rules, output) = self.trigger_rule("Can I get an invite?")
        self.assertEqual(num_rules, 1)
        self.assertRegex(output, rb".*: If you would like an invite to lobste.rs, please look at the chat FAQ first\. .*")

if __name__ == '__main__':
    logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)
    unittest.main()
