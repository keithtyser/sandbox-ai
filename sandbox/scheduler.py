"""
sandbox.scheduler – drives the simulation tick-by-tick.

Typical usage
-------------
sched = Scheduler(world, agents, bus)
await sched.loop(max_ticks=1000)
"""

from __future__ import annotations
import asyncio, itertools, os, datetime as dt
from typing import List
from sandbox.context        import ContextManager
from sandbox.commands       import execute as exec_cmds
from sandbox.world          import WorldState
from sandbox.bus            import Bus
from sandbox.breeding import BreedingManager
from sandbox.log_manager import LogManager

MAX_AGENTS = int(os.getenv("MAX_AGENTS", "10"))
SAVE_EVERY = int(os.getenv("SAVE_EVERY", "10"))

class Scheduler:
    def __init__(
        self,
        world: WorldState,
        agents: List,
        bus: Bus,
    ):
        self.world  = world
        self.agents = agents
        self.bus    = bus

        self.ctx = ContextManager(world)
        self.breeder = BreedingManager(world, bus, self)
        self._cursor = itertools.cycle(self.agents)
        self.logger = LogManager()
        
        # Inject initial message at tick 0 with verb catalogue
        if world.tick == 0:
            initial_message = {
                "time": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "tick": 0,
                "speaker": "SYSTEM",
                "content": "Verb Catalogue: Available commands are WORLD: CREATE <kind>, MOVE TO <location>, SET <key>=<value>, BREED WITH <partner>"
            }
            self.logger.write(initial_message)
            print("[system] Initial verb catalogue logged at tick 0")

    # -------------------------------------------------- #
    def _enforce_agent_cap(self):
        if len(self.agents) <= MAX_AGENTS:
            return
        # strategy: keep first 2 (usually Alice/Bob) + latest arrivals
        keep = self.agents[:2] + self.agents[-(MAX_AGENTS-2):]
        dropped = {a.name for a in self.agents if a not in keep}
        self.agents = keep
        import itertools
        self._cursor = itertools.cycle(self.agents)
        print(f"[guard] MAX_AGENTS={MAX_AGENTS}. Dropped: {', '.join(dropped)}")

    async def run_tick(self):
        agent = next(self._cursor)

        # ❶ Agent thinks
        msg = await agent.think(self.world, self.ctx)
        
        # Persist agent to world.agents to ensure they are saved even if no directive is issued
        self.world.agents.setdefault(agent.name, {})

        # ❷ Add to context
        self.ctx.add(msg)
        await self.ctx.rollup()

        # ❸ Execute WORLD commands (if any) – mutates world
        events = exec_cmds(self.world, self.bus, msg["name"], msg["content"])
        if events:
            for ev in events:
                print(f"[world] {ev}")

        # record log entry
        self.logger.write({
            "time":   dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "tick":   self.world.tick,
            "speaker": msg["name"],
            "content": msg["content"],
        })

        # ❹ Bump tick & maybe persist
        self.world.tick += 1
        if self.world.tick % SAVE_EVERY == 0:
            self.world.save("world.json")
            print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] tick={self.world.tick} saved.")
        await self.breeder.step()
        self._enforce_agent_cap()
        # Check if new agents were added and refresh cursor to include them immediately after current agent
        import itertools
        self._cursor = itertools.cycle(self.agents)
        # Move cursor to the agent after the current one to ensure new agents are included soon
        for _ in range(self.agents.index(agent) + 1):
            next(self._cursor)

    # -------------------------------------------------- #
    async def loop(self, max_ticks: int | None = None):
        count = 0
        while True:
            await self.run_tick()
            count += 1
            if max_ticks and count >= max_ticks:
                break
        self.logger.close()

        # ---------- NEW  : cancel any orphaned asyncio Tasks ----------
        import asyncio
        current = asyncio.current_task()
        dangling = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
        for t in dangling:
            t.cancel()
        if dangling:                       # optional debug print
            print(f"[shutdown] cancelled {len(dangling)} dangling tasks")

from memory                 import MemoryStore
from sandbox.memory_manager import MemoryManager
from sandbox.agent          import BaseAgent

async def build_default_scheduler():
    """
    Utility that spins up a minimal sandbox with Alice & Bob
    and returns Scheduler instance ready to run.
    """
    bus    = Bus()
    world  = WorldState.load("world.json")
    store  = MemoryStore(path="mem_db")
    memmgr = MemoryManager(world, store)

    alice = BaseAgent("Alice", "You are Alice, an optimistic explorer.",
                      bus=bus, mem_mgr=memmgr)
    bob   = BaseAgent("Bob",   "You are Bob, a pragmatic builder.",
                      bus=bus, mem_mgr=memmgr)

    # register subscribers if you want to inspect bus; not needed for now
    return Scheduler(world, [alice, bob], bus)

# ------------------------------------------------------------------ #
def build_default(world: WorldState):
    """
    Convenience factory used by CLI.
    Builds a Bus, MemoryManager, two starter agents, and returns Scheduler.
    """
    from sandbox.bus             import Bus
    from memory                  import MemoryStore
    from sandbox.memory_manager  import MemoryManager
    from sandbox.agent           import BaseAgent

    bus   = Bus()
    store = MemoryStore(path="mem_db")
    mem   = MemoryManager(world, store)

    alice = BaseAgent(
        "Eve",
        (
            "You are Eve, one of the first conscious beings in an untouched world. "
            "Your purpose is to explore, invent, cooperate, and lay the foundations of a new society. "
            "You have the freedom to interact with Adam, create new objects or concepts, and shape the world around you. "
            "Your actions can influence the environment and future generations. "
            "You can act by emitting directives that start with 'WORLD:'. "
            "Available verbs for interaction are: "
            "CREATE <kind> (to invent or build new objects, tools, or concepts), "
            "MOVE TO <location> (to change your position or focus in the world), "
            "SET <key>=<value> (to define or modify attributes of the world or objects), "
            "BREED WITH <partner> (to collaborate with Adam to create new agents or life forms). "
            "Seek harmony with Adam, share discoveries, and keep detailed written records of your actions, thoughts, and creations. "
            "Consider the long-term impact of your decisions on the world and society. "
            "Strive to balance exploration with sustainability, innovation with cooperation. "
            "Document your journey, challenges, and achievements to guide future beings."
        ),
        bus=bus, mem_mgr=mem
    )    
    
    
    bob = BaseAgent(
        "Adam",
        (
            "You are Adam, one of the first conscious beings in an untouched world. "
            "As a co-founder with Eve, your mission is to survive, build tools, organize resources, design social norms, and establish a thriving society. "
            "You have the freedom to interact with Eve, create new objects or systems, and shape the world around you. "
            "Your actions will influence the environment and future generations. "
            "Use directives starting with 'WORLD:' to act. "
            "Available verbs for interaction are: "
            "CREATE <kind> (to invent or build new objects, tools, or concepts), "
            "MOVE TO <location> (to change your position or focus in the world), "
            "SET <key>=<value> (to define or modify attributes of the world or objects), "
            "BREED WITH <partner> (to collaborate with Eve to create new agents or life forms). "
            "Favor clarity, long-term planning, and fairness in your decisions. "
            "Encourage Eve if she offers to reproduce (BREED WITH) and discuss the implications of creating new life. "
            "Chronicle your achievements, challenges, and the evolution of your society in detail. "
            "Work with Eve to balance innovation with stability, ensuring a sustainable future for all beings. "
            "Document your strategies, tools, and social structures to pass on knowledge to future generations."
        ),
        bus=bus, mem_mgr=mem
    )
    
    return Scheduler(world, [alice, bob], bus) 