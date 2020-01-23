import time
import asyncio
import datetime
import discord
from enum import Enum


class Timer(object):
    clock_period = 5

    def __init__(self, name, role, channel, clock_channel, stages=None):
        self.channel = channel
        self.clock_channel = clock_channel
        self.role = role
        self.name = name

        self.start_time = None  # Session start time
        self.current_stage_start = None  # Time at which the current stage started
        self.remaining = None  # Amount of time until the next stage starts
        self.state = TimerState.STOPPED  # Current state of the timer

        self.stages = stages  # List of stages in this timer
        self.current_stage = 0  # Index of current stage

        self.subscribed = {}  # Dict of subbed members, userid maps to (user, lastupdate, timesubbed)

        self.last_clockupdate = 0

        if stages:
            self.setup(stages)

    def __contains__(self, userid):
        """
        Containment interface acts as list of subscribers.
        """
        return userid in self.subscribed

    def setup(self, stages):
        """
        Setup the timer with a list of TimerStages.
        """
        self.state = TimerState.STOPPED

        self.stages = stages
        self.current_stage = 0

        self.start_time = int(time.time())

        self.remaining = stages[0].duration
        self.current_stage_start = int(time.time())

        # Return self for method chaining
        return self

    async def update_clock_channel(self):
        """
        Try to update the name of the status channel with the current status
        """
        # Quit if there's no status channel set
        if self.clock_channel is None:
            return

        # Quit if we aren't due for a clock update yet
        if int(time.time()) - self.last_clockupdate < self.clock_period:
            return

        # Get the name and time strings
        stage_name = self.stages[self.current_stage].name
        remaining_time = self.pretty_remaining()

        # Update the channel name, or quit silently if something goes wrong.
        try:
            await self.clock_channel.edit(name="{} - {}".format(stage_name, remaining_time))
        except Exception:
            pass

    def pretty_remaining(self):
        """
        Return a formatted version of the time remaining until the next stage.
        """
        diff = self.remaining
        diff = max(diff, 0)
        hours = diff // 3600
        minutes = (diff % 3600) // 60
        seconds = diff % 60

        return "{:02d}:{:02d}:{:02d}".format(hours, minutes, seconds)

    def pretty_pinstatus(self):
        """
        Return a formatted status string for use in the pinned status message.
        """
        if self.state in [TimerState.RUNNING, TimerState.PAUSED]:
            # Collect the component strings and data
            current_stage_name = self.stages[self.current_stage].name
            remaining = self.pretty_remaining()

            subbed_names = [m.member.name for m in self.subscribed.values()]
            subbed_str = "```{}```".format(", ".join(subbed_names)) if subbed_names else "*No subscribers*"

            # Create a list of lines for the stage string
            longest_stage_len = max(len(stage.name) for stage in self.stages)
            stage_format = "`{{prefix}}{{name:>{}}}:` {{dur}} min  {{current}}".format(longest_stage_len)

            stage_str_lines = [
                stage_format.format(
                    prefix="->" if i == self.current_stage else "​  ",
                    name=stage.name,
                    dur=stage.duration,
                    current="(**{}**)".format(remaining) if i == self.current_stage else ""
                ) for i, stage in enumerate(self.stages)
            ]
            # Create the stage string itself
            stage_str = "\n".join(stage_str_lines)

            # Create the final formatted status string
            status_str = ("**{name}** ({current_stage_name}){paused}\n"
                          "{stage_str}\n"
                          "{subbed_str}").format(name=self.name,
                                                 paused=" ***Paused***" if self.state == TimerState.PAUSED else "",
                                                 current_stage_name=current_stage_name,
                                                 stage_str=stage_str,
                                                 subbed_str=subbed_str)
        elif self.state == TimerState.STOPPED:
            status_str = "**{}**: *Not set up.*".format(self.name)
        return status_str

    def pretty_summary(self):
        """
        Return a one line summary status message.
        """
        pass

    async def change_stage(self, stage_index, notify=True, inactivity_check=True, report_old=True):
        """
        Advance the timer to the new stage.
        """
        # Update clocked times for all the subbed users
        [subber.touch() for subber in self.subscribed]

        stage_index = stage_index % len(self.stages)
        current_stage = self.stages[self.current_stage]
        new_stage = self.stages[stage_index]

        # Handle notifications
        if notify:
            old_stage_str = "**{}** finished! ".format(current_stage.name) if report_old else ""
            out_msg = await self.channel.send(
                ("{}\n{}Starting **{}** ({} minutes). {}\n"
                 "Please react to this message to register your presence!").format(
                     self.role.mention,
                     old_stage_str,
                     new_stage.name,
                     new_stage.duration,
                     new_stage.message
                 )
            )
            try:
                await out_msg.add_reaction("✅")
            except Exception:
                pass

        self.current_stage = stage_index
        self.current_stage_start = int(time.time())
        self.remaining = self.stages[stage_index].duration * 60

        # Handle inactivity
        pass

    async def start(self):
        """
        Start or restart the timer.
        """
        await self.change_stage(0, report_old=False)
        self.state = TimerState.RUNNING
        asyncio.ensure_future(self.runloop())

    async def runloop(self):
        while self.state == TimerState.RUNNING:
            self.remaining = int(60*self.stages[self.current_stage].duration - (time.time() - self.current_stage_start))
            if self.remaining <= 0:
                await self.change_stage(self.current_stage + 1)

            await self.update_clock_channel()
            await asyncio.sleep(1)


class TimerState(Enum):
    """
    Enum representing the current running state of the timer.
    STOPPED: The timer either hasn't been set up, or has been stopped externally.
    RUNNING: The timer is running normally.
    PAUSED: The timer has been paused by a user.
    """
    STOPPED = 1
    RUNNING = 2
    PAUSED = 3


class TimerStage(object):
    """
    Small data class to encapsualate a "stage" of a timer.

    Parameters
    ----------
    name: str
        The human readable name of the stage.
    duration: int
        The number of minutes the stage lasts for.
    message: str
        An optional message to send when starting this stage.
    focus: bool
        Whether `focus` mode is set for this stage.
    modifiers: Dict(str, bool)
        An unspecified collection of stage modifiers, stored for external use.
    """
    __slots__ = ('name', 'message', 'duration', 'focus', 'modifiers')

    def __init__(self, name, duration, message="", focus=False, **modifiers):
        self.name = name
        self.duration = duration
        self.message = message

        self.focus = focus

        self.modifiers = modifiers


class TimerChannel(object):
    """
    A data class representing a guild channel bound to (potentially) several timers.

    Parameters
    ----------
    channel: discord.Channel
        The bound discord guild channel
    timers: List(Timer)
        The timers bound to the channel
    msg: discord.Message
        A valid and current discord Message in the channel.
        Holds the updating timer status messages.
    """
    __slots__ = ('channel', 'timers', 'msg')

    def __init__(self, channel):
        self.channel = channel

        self.timers = []
        self.msg = None

    async def update(self):
        """
        Create or update the channel status message.
        """
        messages = [timer.pretty_pinstatus() for timer in self.timers]
        if messages:
            desc = "\n\n".join(messages)
            embed = discord.Embed(
                title="Pomodoro Timer Status",
                description=desc,
                timestamp=datetime.datetime.now()
            )
            if self.msg is not None:
                try:
                    await self.msg.edit(embed=embed)
                except Exception:
                    pass
            else:
                # Attempt to generate a new message
                try:
                    self.msg = await self.channel.send(embed=embed)
                except discord.Forbidden:
                    await self.channel.send("I require permission to send embeds in this channel! Stopping all timers.")
                    for timer in self.timers:
                        timer.state = TimerState.STOPPED

                # Pin the message
                try:
                    await self.msg.pin()
                except Exception:
                    pass


class TimerSubscriber(object):
    __slots__ = (
        'member',
        'timer',
        'interface',
        'client',
        'id',
        'time_joined',
        'last_updated',
        'clocked_time',
        'active',
        'last_seen',
        'warnings'
    )

    def __init__(self, member, timer, interface):
        self.member = member
        self.timer = timer
        self.interface = interface

        self.client = interface.client
        self.id = member.id

        now = int(time.time())
        self.time_joined = now

        self.last_updated = now
        self.clocked_time = 0
        self.active = True

        self.last_seen = now
        self.warnings = 0

    def unsub(self):
        self.interface.unsub(self.id)

    def bump(self):
        self.last_seen = int(time.time())
        self.warnings = 0

    def touch(self):
        """
        Update the clocked time based on the active status.
        """
        now = int(time.time())
        self.clocked_time += (now - self.last_updated) if self.active else 0
        self.last_updated = now

    def session_data(self):
        """
        Return session data in a format compatible with the registry.
        """
        self.touch()

        return (
            self.id,
            self.member.guild.id,
            self.timer.role.id,
            self.time_joined,
            self.clocked_time
        )

    def serialise(self):
        return (
            self.id,
            self.member.guild.id,
            self.timer.role.id,
            self.time_joined,
            self.last_updated,
            self.time_subbed,
            self.last_seen,
            self.warnings
        )

    @classmethod
    def deserialise(cls, member, timer, interface, data):
        self = cls(member, timer, interface)

        self.time_joined = data[3]
        self.last_updated = data[4]
        self.time_subbed = data[5]
        self.last_seen = data[6]
        self.warnings = data[7]

        return self
