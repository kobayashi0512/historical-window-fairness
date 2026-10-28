#!/usr/bin/env python3
"""Online prefix-feasible edge orientation via paired incidences.

Each edge arrival is a q=2 row with half entitlement at each endpoint. At each
vertex, pair incident edges in arrival order: (1,2), (3,4), ... . Every pair is
constrained to contribute opposite signs at that vertex. The resulting
constraint graph has maximum degree two. When a new edge joins two constraint
components with incompatible signs, flip the smaller component (all its old
edge orientations), then merge. This is an executable research prototype;
the accompanying memo contains the proof obligations and limits.
"""
import argparse
import json
import random
import time
from collections import defaultdict
from pathlib import Path


def b_at(edge, vertex):
    a, c = edge
    if vertex == a:
        return 1
    if vertex == c:
        return -1
    raise ValueError("vertex is not incident to edge")


class PrefixPairing:
    def __init__(self, n):
        self.n = n
        self.edges = []
        self.z = []  # +1 assigns to the lower endpoint; -1 to the higher.
        self.constraints = []  # (old edge id, new edge id, z_new / z_old)
        self.pending = [None]*n
        self.component_of = []
        self.members = {}
        self.flip_costs = []
        self.flip_components = []

    def components(self):
        adj = [[] for _ in self.edges]
        for a, b, _ in self.constraints:
            adj[a].append(b)
            adj[b].append(a)
        comp = [-1]*len(self.edges)
        members = []
        for s in range(len(self.edges)):
            if comp[s] >= 0:
                continue
            cid = len(members)
            stack = [s]
            comp[s] = cid
            nodes = []
            while stack:
                x = stack.pop()
                nodes.append(x)
                for y in adj[x]:
                    if comp[y] < 0:
                        comp[y] = cid
                        stack.append(y)
            members.append(nodes)
        return comp, members

    def add(self, u, v):
        if not (0 <= u < v < self.n):
            raise ValueError("edges must be normalized as 0 <= u < v < n")
        edge = (u, v)
        new_id = len(self.edges)
        endpoints = (u, v)
        pending = [self.pending[x] for x in endpoints]
        pair_constraints = []
        for vertex, old_id in zip(endpoints, pending):
            if old_id is not None:
                # b_old*z_old + b_new*z_new = 0
                rel = -b_at(self.edges[old_id], vertex)*b_at(edge, vertex)
                pair_constraints.append((old_id, rel))

        old_component_ids = []
        for old_id, _ in pair_constraints:
            cid = self.component_of[old_id]
            if cid not in old_component_ids:
                old_component_ids.append(cid)
        merge_base = None
        if len(pair_constraints) == 2:
            (a, rel_a), (b, rel_b) = pair_constraints
            ca, cb = self.component_of[a], self.component_of[b]
            if ca == cb:
                need_a = rel_a*self.z[a]
                need_b = rel_b*self.z[b]
                if need_a != need_b:
                    raise AssertionError("new cycle constraints are inconsistent")
                z_new = need_a
                merge_base = ca
            else:
                need_a = rel_a*self.z[a]
                need_b = rel_b*self.z[b]
                if need_a != need_b:
                    # Flip the smaller component; all constraints internal to it
                    # remain satisfied, and the two required new signs now agree.
                    flip_c = ca if len(self.members[ca]) <= len(self.members[cb]) else cb
                    flipped = tuple(sorted(self.members[flip_c]))
                    for i in flipped:
                        self.z[i] *= -1
                    self.flip_costs.append(len(flipped))
                    self.flip_components.append(flipped)
                    if flip_c == ca:
                        need_a *= -1
                    else:
                        need_b *= -1
                if need_a != need_b:
                    raise AssertionError("small-component flip failed to align constraints")
                z_new = need_a
                merge_base = max((ca,cb),key=lambda c:(len(self.members[c]),-c))
        elif len(pair_constraints) == 1:
            a, rel = pair_constraints[0]
            z_new = rel*self.z[a]
            merge_base = self.component_of[a]
        else:
            z_new = 1

        # Pairing is per vertex: an old pending incidence is consumed, otherwise
        # this edge becomes that vertex's unmatched last incidence.
        for vertex, old_id in zip(endpoints, pending):
            self.pending[vertex] = new_id if old_id is None else None

        self.edges.append(edge)
        self.z.append(z_new)
        new_id = len(self.edges)-1
        if len(old_component_ids) == 2:
            ca, cb = old_component_ids
            base = merge_base
            other = cb if base == ca else ca
            moved = self.members.pop(other)
            self.members[base].update(moved)
            for i in moved:
                self.component_of[i] = base
            self.members[base].add(new_id)
            self.component_of.append(base)
        elif len(old_component_ids) == 1:
            base = old_component_ids[0]
            self.members[base].add(new_id)
            self.component_of.append(base)
        else:
            self.members[new_id] = {new_id}
            self.component_of.append(new_id)
        for old_id, rel in pair_constraints:
            self.constraints.append((old_id, new_id, rel))
        return z_new

    def choices(self):
        return [u if z == 1 else v for (u, v), z in zip(self.edges, self.z)]


def valid_prefix_schedule(edges, choices, n):
    ent = [0]*n
    alloc = [0]*n
    for (u, v), chosen in zip(edges, choices):
        if chosen not in (u, v):
            return False
        ent[u] += 1
        ent[v] += 1
        alloc[chosen] += 1
        if any(not (ent[i]//2 <= alloc[i] <= (ent[i]+1)//2) for i in range(n)):
            return False
    return True


def run_stream(n, edges):
    alg = PrefixPairing(n)
    snapshots = []
    for t, (u, v) in enumerate(edges):
        alg.add(*tuple(sorted((u, v))))
        choices = alg.choices()
        ok = valid_prefix_schedule(alg.edges, choices, n)
        if not ok:
            return {"ok": False, "failure_t": t, "edges": alg.edges,
                    "choices": choices, "z": alg.z,
                    "constraints": alg.constraints,
                    "flips": alg.flip_components}
        snapshots.append({"t": t+1, "choices": choices,
                          "cumulative_flip_cost": sum(alg.flip_costs),
                          "new_flip_cost": alg.flip_costs[-1] if len(alg.flip_costs)>len(snapshots) else 0})
    return {"ok": True, "n": n, "T": len(edges),
            "edges": alg.edges, "choices": alg.choices(),
            "flip_costs": alg.flip_costs,
            "total_old_row_flips": sum(alg.flip_costs),
            "max_single_update_flips": max(alg.flip_costs, default=0),
            "component_flip_events": len(alg.flip_costs),
            "max_edge_flip_multiplicity": max((sum(i in c for c in alg.flip_components) for i in range(len(edges))), default=0),
            "all_prefixes_revalidated_from_assignments": True}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--mode", choices=("random", "path-trap", "adversarial-edge-order"), default="random")
    ap.add_argument("--length", type=int, default=200)
    ap.add_argument("--seeds", type=int, default=100)
    ap.add_argument("--seed", type=int, default=20261012)
    ap.add_argument("--simple", action="store_true")
    args = ap.parse_args()
    start = time.time()
    rng = random.Random(args.seed)
    if args.mode == "path-trap":
        L = args.length
        n = 2*(L+1)
        edges = [(i, i+1) for i in range(L)] + [(L+1+i, L+2+i) for i in range(L)]
        state = PrefixPairing(n)
        for e in edges:
            state.add(*e)
        z_before = state.z.copy()
        d = [0]*n
        for (u,v),z in zip(state.edges,state.z):
            d[u]+=z; d[v]-=z
        plus_a = next(x for x in (0,L) if d[x]==1)
        plus_b = next(x for x in (L+1,2*L+1) if d[x]==1)
        final_edge = tuple(sorted((plus_a,plus_b)))
        state.add(*final_edge)
        state.edges = list(state.edges)
        certificate = {"length_each_path":L,"n":n,"edges_before_final":edges,
                      "positive_endpoints":(plus_a,plus_b),"adaptive_final_edge":final_edge,
                      "final_flip_cost":state.flip_costs[-1] if len(state.flip_costs)>0 else 0,
                      "cumulative_flip_cost":sum(state.flip_costs),
                      "final_schedule_prefix_valid":valid_prefix_schedule(state.edges,state.choices(),n),
                      "flips_before_final_update":len(z_before)}
        result = {"mode":args.mode,"result":certificate}
    else:
        if args.mode == "adversarial-edge-order":
            # A deterministic seed family that makes two long components first,
            # then repeatedly joins currently same-sign active vertices where possible.
            edges = []
            state = PrefixPairing(args.n)
            all_edges = [(u,v) for u in range(args.n) for v in range(u+1,args.n)]
            if args.simple:
                rng.shuffle(all_edges)
                edges = all_edges[:min(args.length,len(all_edges))]
            else:
                for t in range(args.length):
                    d=[0]*args.n
                    for (u,v),z in zip(state.edges,state.z): d[u]+=z; d[v]-=z
                    pairs=all_edges
                    def score(e):
                        a,b=e
                        return (0 if d[a]==d[b] and abs(d[a])==1 else
                                1 if d[a]==0 and d[b]==0 else
                                2 if abs(d[a])==1 and abs(d[b])==1 else 3)
                    edge=min(pairs,key=score)
                    edges.append(edge)
                    state.add(*edge)
        else:
            edges = None
        if args.mode == "random":
            runs=[]
            for si in range(args.seeds):
                local=random.Random(args.seed+si)
                pool=[(u,v) for u in range(args.n) for v in range(u+1,args.n)]
                if args.simple:
                    local.shuffle(pool)
                seq=[]
                for t in range(args.length):
                    if args.simple:
                        if not pool: break
                        seq.append(pool.pop())
                    else:
                        seq.append(tuple(sorted(local.sample(range(args.n),2))))
                runs.append(run_stream(args.n,seq))
            result={"mode":args.mode,"n":args.n,"r":None,"seeds":args.seeds,
                    "length":args.length,"simple":args.simple,
                    "runs":runs,"all_runs_valid":all(r["ok"] for r in runs),
                    "total_flip_counts":[r.get("total_old_row_flips") for r in runs],
                    "max_update_flip_counts":[r.get("max_single_update_flips") for r in runs]}
        else:
            result={"mode":args.mode,"simple":args.simple,
                    "run":run_stream(args.n,edges)}
    result["elapsed_seconds"]=round(time.time()-start,6)
    root=Path(__file__).resolve().parent
    out=root/f"prefix_pairing_{args.mode}_n{args.n}_L{args.length}_S{args.seeds}_seed{args.seed}.json"
    out.write_text(json.dumps(result,indent=2)+"\n")
    if args.mode=="random":
        print(json.dumps({k:v for k,v in result.items() if k!="runs"},indent=2))
    else:
        print(json.dumps(result,indent=2))
    print(f"saved={out}")


if __name__=="__main__":
    main()
