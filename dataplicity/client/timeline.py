"""
Creates a database of timestamped events.

"""

from dataplicity import constants

from time import time
from random import randint
from json import dumps, loads
from operator import itemgetter
import os.path

from fs.osfs import OSFS
from fs.errors import FSError


import logging
log = logging.getLogger('dataplicity')

# maps event types to an event class
_event_registry = {}


def register_event(event_type):
    """Class decorator to register a new event class"""
    def class_deco(cls):
        cls.event_type = event_type
        _event_registry[event_type] = cls
        return cls
    return class_deco


class TimelineError(Exception):
    pass


class UnknownTimelineError(TimelineError):
    pass


class UnknownEventError(TimelineError):
    pass


class TimelineFullError(TimelineError):
    pass


class Event(object):
    """base class for events"""
    def __init__(self, timeline, event_id, timestamp, *args, **kwargs):
        self.timeline = timeline
        self.event_id = event_id
        self.timestamp = timestamp
        self.attachments = []
        self.init(*args, **kwargs)
        super(Event, self).__init__()

    def __repr__(self):
        return "<event {} {}>".format(self.event_type, self.event_id)

    def serialize(self):
        raise NotImplementedError

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.write()

    def attach(self, filename, name=None):
        if name is None:
            name = filename
        # TODO: attachments
        print "Attaching", filename
        return self

    def write(self):
        """Write the event (called automatically)"""
        self.timeline._write_event(self.event_id, self)
        return self


@register_event("TEXT")
class TextEvent(Event):

    def init(self, title='', text='', text_format="TEXT"):
        self.title = title
        self.text = text
        self.text_format = text_format

    def serialize(self):
        return {"timestamp": self.timestamp,
                "event_type": self.event_type,
                "title": self.title,
                "text": self.text,
                "text_format": self.text_format}


class TimelineManager(object):
    """Manages a collection of timelines"""

    def __init__(self, path):
        self.path = path
        self.timelines = {}

    def __nonzero__(self):
    	return bool(self.timelines)

    def __iter__(self):
    	return self.timelines.itervalues()

    @classmethod
    def init_from_conf(cls, client, conf):
        timelines_path = conf.get('timelines', 'path', constants.TIMELINE_PATH)
        timelines_path = os.path.join(timelines_path, client.device_class)
        timeline_manager = cls(timelines_path)

        for section, name in conf.qualified_sections('timeline'):
            max_events = conf.get(section, 'max_events', None)
            timeline_manager.new_timeline(name, max_events=max_events)
        return timeline_manager

    def new_timeline(self, name, max_events=None):
        """Create a new timeline and store it"""
        path = os.path.join(self.path, name)
        timeline = Timeline(path, name, max_events=max_events)
        self.timelines[timeline.name] = timeline

    def get_timeline(self, timeline_name):
        try:
            timeline = self.timelines[timeline_name]
        except KeyError:
            raise UnknownTimelineError("No timeline called '{}' exists".format('timeline_name'))
        else:
            return timeline


class Timeline(object):

    def __init__(self, path, name, max_events=None):
        self.path = path
        self.name = name
        self.fs = OSFS(path, create=True)
        self.max_events = max_events

    def __repr__(self):
        return "Timeline({!r}, {!r}, max_events={!r})".format(self.path, self.name, self.max_events)

    # @classmethod
    # def init_from_conf(cls, client, conf):
    #     timeline_path = conf.get('timelines', 'path', constants.TIMELINE_PATH)
    #     max_events = conf.get('timeline', 'max_events', None)
    #     return Timeline(timeline_path, max_events=max_events)

    def new_event(self, event_type, timestamp=None, *args, **kwargs):
        """Create and return an event, to be used as a context manager"""
        if self.max_events is not None:
            size = len(self.fs.listdir(wildcard="*.json"))
            if size >= self.max_events:
                raise TimelineFullError("The timeline has reached its maximum size")

        if timestamp is None:
            timestamp = int(time() * 1000.0)
        try:
            event_cls = _event_registry[event_type]
        except KeyError:
            raise UnknownEventError("No event type '{}'".format(event_type))

        # Make an event id that we can be confident it's unique
        token = str(randint(0, 2 ** 31))
        event_id = "{}_{}_{}".format(event_type, timestamp, token)
        event = event_cls(self, event_id, timestamp, *args, **kwargs)
        log.debug('new event {!r}'.format(event))
        return event

    def add_event(self, event_type, timestamp=None, *args, **kwargs):
        """Add a new event of a given type to the timeline"""
        event = self.new_event(event_type, timestamp=timestamp, *args, **kwargs)
        event.write()
        return self

    def get_events(self, sort=True):
        """Get all accumulated events"""
        events = []
        for event_filename in self.fs.listdir(wildcard="*.json"):
            with self.fs.open(event_filename, 'rb') as f:
                event = loads(f.read())
                events.append(event)
        if sort:
            # sort by timestamp
            events.sort(key=itemgetter('timestamp'))
        return events

    def clear_all(self):
        """Clear all stored events"""
        for filename in self.fs.listdir(wildcard="*.json"):
            try:
                self.fs.remove(filename)
            except FSError:
                pass

    def clear_events(self, event_ids):
        """Clear any events that have been processed"""
        for event_id in event_ids:
            filename = "{}.json".format(event_id)
            try:
                self.fs.remove(filename)
            except FSError:
                pass

    def _write_event(self, event_id, event):
        if hasattr(event, 'serialize'):
            event = event.serialize()
        event['_id'] = event_id
        event_json = dumps(event)
        filename = "{}.json".format(event_id)
        with self.fs.open(filename, 'wb') as f:
            f.write(event_json)


if __name__ == "__main__":

    timelines = TimelineManager('/tmp/timeline')
    timelines.new_timeline('test')

    timeline = timelines.get_timeline('test')
    print timeline
    timeline.add_event('TEXT', text="Hello, World!", title="Greeting")

    with timeline.new_event('TEXT', text="Frodo", title="Hobbits") as event:
        event.attach('frodo.jpg', name="photo")

    events = timeline.get_events()
    from pprint import pprint
    pprint(events)
