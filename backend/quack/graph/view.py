"""Batch view: what was read from the store plus changes already made in this batch (Т-§2.4)."""

from __future__ import annotations

from dataclasses import dataclass, field

from quack.config import FoldParams
from quack.graph.memory import InMemoryGraph, follow_alias
from quack.graph.mutations import Mutation, RebindNodeRel
from quack.graph.types import SubGraph


@dataclass
class BatchView:
    learner_id: str
    params: FoldParams
    graph: InMemoryGraph = field(default_factory=InMemoryGraph)
    aliases: dict[str, str] = field(default_factory=dict)
    mutations: list[Mutation] = field(default_factory=list)

    def resolve(self, node_id: str) -> str:
        """Follow Alias redirects (transitively) to the live node id."""
        return follow_alias(self.aliases, node_id)

    def emit(self, mutation: Mutation) -> None:
        """Record the mutation and apply it to the in-memory graph."""
        self.mutations.append(mutation)
        self.graph.apply_mutation(mutation)
        if isinstance(mutation, RebindNodeRel) and mutation.source_id != mutation.target_id:
            for source, target in list(self.aliases.items()):
                if target == mutation.source_id:
                    self.aliases[source] = mutation.target_id
            self.aliases[mutation.source_id] = mutation.target_id

    def load(self, subgraph: SubGraph) -> None:
        """Merge a store read into the view without overriding batch changes."""
        for node in subgraph.nodes:
            if node.label == "Alias":
                target = node.props.get("targetId")
                if isinstance(target, str) and node.id not in self.aliases:
                    self.aliases[node.id] = target
        self.graph.insert_subgraph(subgraph, self.aliases)
