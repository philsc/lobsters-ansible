#!/usr/bin/env python3

import os
import unittest
from unittest import mock
import re
import logging
import sys
from tempfile import TemporaryDirectory
from typing import Any, Tuple

import sopel.bot
import sopel.config
import sopel.tests.factories
import sopel.trigger
from sopel.loader import is_triggerable, clean_module

# Import the module under test.
import invite


# A minimal config that lets us unit test the invite behaviour.
TEST_CONFIG="""
[core]
owner = Bar
nick = Sopel
enable = coretasks
"""

class MockConfig(sopel.config.Config):
    """Mocks a sopel config.

    Ideally we'd use upstream's sopel.tests.pytest_plugins.configfactory
    instead. However, as the name implies, it requires pytest. For simplicity
    let's use the unittest module. That means re-implementing a couple of
    things here.

    The intended use case is to instantiate one of these at the beginning of
    each test. Make sure to call cleanup() at the end of each test.
    """

    def __init__(self):
        self._tmpdir = TemporaryDirectory()
        self._tmpfile = os.path.join(self._tmpdir.name, "test.cfg")
        with open(self._tmpfile, "w") as file:
            file.write(TEST_CONFIG)
        super().__init__(self._tmpfile)

    def cleanup(self):
        """Cleans up the temporary files for the mocked confic.

        Call this function at the end of each test.
        """
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
    """The test fixture for validating invite.py."""

    # TODO(phil): Allow testers to customize the channel names and nicks
    # involved here.

    def setUp(self) -> None:
        """Sets up the test."""
        self.time = MockTime()
        self.config = MockConfig()
        botfactory = sopel.tests.factories.BotFactory()
        self.bot = botfactory.preloaded(self.config)

        self.callables, self.jobs, self.shutdowns, self.urls = clean_module(invite, self.bot.config)

    def tearDown(self) -> None:
        """Tears down the test."""
        self.config.cleanup()

    def _match(self, _: Any) -> None:
        """Helper for mocking sopel triggers."""
        # I think we're simulating a regex search result here. Not sure yet.
        # Returning None seems to make sopel happy so do that for now.
        return None

    def trigger_event(self, event_name: str) -> Tuple[int, bytes]:
        """Triggers an event registered via @sopel.module.event(...).

        If you have the following in your module:

            @sopel.module.event("JOIN")
            def announce_joins(bot, trigger):
                bot.reply("Welcome, %s" % trigger.nick)

        then you can trigger that in your unit test by calling this function
        with "JOIN" as an argument. Note that if you have multiple function
        trigger on the same event, calling this function will invoke all the
        relevant functions.

        Args:
            event_name: The name of the event to simulate. E.g. "JOIN".

        Returns:
            A tuple containing the number of functions that were triggered and
            the raw output of the bot.
        """
        # TODO(phil): Can we de-duplicate this and trigger_rule?
        full_message = ':hostmask %s #channel user :Some text' % event_name
        pretrigger = sopel.trigger.PreTrigger(self.bot.nick, full_message)
        trigger = sopel.trigger.Trigger(self.bot.config, pretrigger, self._match)
        wrapper = sopel.bot.SopelWrapper(self.bot, trigger)
        num_events = 0

        for function in self.callables:
            if not hasattr(function, "event"):
                continue

            for event in function.event:
                if event == event_name:
                    function(wrapper, trigger)
                    num_events += 1

        return (num_events, b"".join(wrapper.backend.message_sent))

    def trigger_rule(self, message: str) -> Tuple[int, bytes]:
        """Triggers a rule registered via @sopel.module.rule(...).

        If you have the following in your module:

            @sopel.module.rule(".* foobar .*")
            def reply_to_foobar(bot, trigger):
                bot.reply("Someone said 'foobar'!")

        then you can trigger that in your unit test by calling this function
        with "pretending to say foobar here" as an argument. Note that if you
        have multiple function with rules then all relevant ones will get
        triggered.

        Args:
            message: The message to mock being sent on the channel.

        Returns:
            A tuple containing the number of functions that were triggered and
            the raw output of the bot.
        """
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
        """Validates some basic assumptions around sopel.

        This test doesn't really serve much purpose, but was super useful in
        debugging what @sopel.module annotations change sopel behaviour.
        """
        self.assertTrue(is_triggerable(invite.invite))
        self.assertTrue(is_triggerable(invite.note))


    @mock.patch("invite.time.time")
    def test_successful_hint(self, mock_time):
        """Validates that users joining and asking for invites get a hint."""
        mock_time.side_effect = self.time.time

        (num_events, output) = self.trigger_event("JOIN")
        self.assertEqual(num_events, 1)
        self.assertEqual(output, b"")

        (num_rules, output) = self.trigger_rule("Can I get an invite?")
        self.assertEqual(num_rules, 1)
        self.assertRegex(output, rb".*: If you would like an invite to lobste.rs, please look at the chat FAQ first\. .*")

    @mock.patch("invite.time.time")
    def test_no_hint_after_timeout(self, mock_time):
        """Validates that only recent users get the FAQ hint."""
        mock_time.side_effect = self.time.time

        (num_events, output) = self.trigger_event("JOIN")
        self.assertEqual(num_events, 1)
        self.assertEqual(output, b"")

        # Wait over an hour.
        self.time.sleep(4000)

        # Expect silence.
        (num_rules, output) = self.trigger_rule("Can I get an invite?")
        self.assertEqual(num_rules, 1)
        self.assertEqual(output, b"")

    @mock.patch("invite.time.time")
    def test_no_hint_without_join(self, mock_time):
        """Validates that without a user's join time the bot stays quiet."""
        mock_time.side_effect = self.time.time

        # Expect silence.
        (num_rules, output) = self.trigger_rule("Can I get an invite?")
        self.assertEqual(num_rules, 1)
        self.assertEqual(output, b"")


if __name__ == '__main__':
    logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)
    unittest.main()
