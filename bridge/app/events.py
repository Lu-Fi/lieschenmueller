import asyncio


class Hub:
    def __init__(self):
        self.subs = set()

    def subscribe(self):
        q = asyncio.Queue(maxsize=500)
        self.subs.add(q)
        return q

    def unsubscribe(self, q):
        self.subs.discard(q)

    def publish(self, ev):
        for q in list(self.subs):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass


hub = Hub()
