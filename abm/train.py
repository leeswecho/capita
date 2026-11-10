"""Train the decision network (engine/decisions.py) on the agent-based model's records (abm.py).

    python abm/train.py [abm/data/train.npz ...] [--epochs 60] [--out abm/data/decision_net.npz]

Each recorded year is a segment's situation (decisions.features()) and what its people did: hours a day of
sleep, work and childcare at each age, and births per person at each age. The network learns to give the
same from the situation alone. Hours are fitted by squared error and births by Poisson likelihood, each age
weighted by how many people it has (a handful of people make noisy figures). A tenth of the scenarios
are held out; the report compares the network with the ABM and the placeholder decide() on them. The
weights are saved for decisions.network_decide(), which runs them with NumPy alone, and checked against
it.
"""
import argparse
import os
import sys
import time

import numpy as np
import torch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import abm  # noqa: E402  (puts engine/ and server/ on the path)
import decisions  # noqa: E402
import engine  # noqa: E402
from decisions import ACTIVITIES, AGES, AGE_CURVES, BIRTH_SCALE, OUTPUTS  # noqa: E402

OUT = os.path.join(abm.DATA, "decision_net.npz")


def load(files):
    """The records of several runs as one, with scenario ids made distinct across runs."""
    runs = [dict(np.load(f)) for f in files]
    keys = [k for k, v in runs[0].items() if v.ndim and len(v) == len(runs[0]["scenario"])]
    offset, parts = 0, []
    for d in runs:
        d["scenario"] = d["scenario"] + offset
        offset = int(d["scenario"].max()) + 1
        parts.append(d)
    return {k: np.concatenate([d[k] for d in parts]) for k in keys}


def targets(d):
    """What the people did: hours [R, AGES, 3] of sleep, work and childcare; births [R, AGES]; and the
    weight of each age, its people [R, AGES]."""
    rows = np.arange(len(d["people"]))
    hours = np.stack([d["hours"][:, :, ACTIVITIES.index("sleep")], d["hours"][rows, :, d["livelihood"]],
                      d["hours"][:, :, ACTIVITIES.index("childcare")]], axis=2)
    return hours.astype(np.float32), d["birth_rate"].astype(np.float32), d["people"].astype(np.float32)


class Net(torch.nn.Module):
    """Dense layers with ReLU between them, then each output = softplus(coefficients . age curves), as
    decisions.network_decide() computes it."""

    def __init__(self, n_in, width, depth):
        super().__init__()
        sizes = [n_in] + [width] * depth + [len(OUTPUTS) * AGE_CURVES]
        self.layers = torch.nn.ModuleList(torch.nn.Linear(a, b) for a, b in zip(sizes, sizes[1:]))
        self.register_buffer("curves", torch.tensor(decisions.age_curves(), dtype=torch.float32))

    def forward(self, x):
        for k, layer in enumerate(self.layers):
            x = layer(x)
            if k < len(self.layers) - 1:
                x = torch.relu(x)
        v = torch.nn.functional.softplus(x.view(len(x), len(OUTPUTS), AGE_CURVES) @ self.curves)
        return v[:, :3].transpose(1, 2), v[:, 3] * BIRTH_SCALE    # hours [N, AGES, 3], births [N, AGES]


def loss_fn(hours, births, t_hours, t_births, w, birth_weight):
    weight = w / w.sum(dim=1, keepdim=True).clamp(min=1)
    mse = (((hours - t_hours) ** 2).sum(dim=2) * weight).sum(dim=1).mean()
    expected, seen = births * w, t_births * w                  # births in the year
    poisson = ((expected - seen * torch.log(expected + 1e-6)) / w.sum(dim=1, keepdim=True).clamp(min=1)).sum(dim=1).mean()
    return mse + birth_weight * poisson, mse


def report(d, rows, chosen, placeholder):
    """Hours and births by way of life and age group, on the rows given: the ABM, the network and the
    placeholder."""
    bands = [("5-14", 5, 15), ("15-49", 15, 50), ("50-64", 50, 65), ("65+", 65, AGES)]
    for name, liv in (("foraging", "subsistence"), ("farming", "farming")):
        r = rows[d["livelihood"][rows] == ACTIVITIES.index(liv)]
        if not len(r):
            continue
        people = d["people"][r]
        print(f"\n{name}, {len(r)} held-out years from {len(np.unique(d['scenario'][r]))} scenarios")
        print(f"  {'':28}{'ABM':>10}{'network':>10}{'placeholder':>13}")
        for label, col in (("work", ACTIVITIES.index(liv)), ("childcare", ACTIVITIES.index("childcare"))):
            for band, lo, hi in bands:
                mean = lambda h: (h[:, lo:hi, col] * people[:, lo:hi]).sum() / people[:, lo:hi].sum()
                print(f"  {label + ' h/day, ages ' + band:28}{mean(d['hours'][r]):10.2f}"
                      f"{mean(chosen.hours[r]):10.2f}{mean(placeholder.hours[r]):13.2f}")
        cbr = lambda rate: 1000 * (rate * people).sum() / people.sum()
        tfr = lambda rate: 2 * rate.sum(axis=1).mean()
        print(f"  {'births per 1,000 a year':28}{cbr(d['birth_rate'][r]):10.1f}{cbr(chosen.birth_rate[r]):10.1f}"
              f"{cbr(placeholder.birth_rate[r]):13.1f}")
        print(f"  {'children per woman':28}{tfr(d['birth_rate'][r]):10.1f}{tfr(chosen.birth_rate[r]):10.1f}"
              f"{tfr(placeholder.birth_rate[r]):13.1f}")
        # How closely it follows each year's work, segment by segment (not just on average).
        work = lambda h: (h[r][:, 15:65, ACTIVITIES.index(liv)] * people[:, 15:65]).sum(axis=1) / people[:, 15:65].sum(axis=1)
        a, n = work(d["hours"]), work(chosen.hours)
        print(f"  {'adult work, year by year':28}  r = {np.corrcoef(a, n)[0, 1]:.3f}, "
              f"mean error {np.abs(a - n).mean():.2f} h/day")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*", default=[os.path.join(abm.DATA, "train.npz")])
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--width", type=int, default=256)
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--birth-weight", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    torch.manual_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"

    d = load(args.files)
    situation = abm.situation(d)
    x = decisions.features(situation)
    t_hours, t_births, w = targets(d)
    scenarios = np.unique(d["scenario"])
    held = np.random.default_rng(args.seed).choice(scenarios, size=max(1, len(scenarios) // 10), replace=False)
    test = np.flatnonzero(np.isin(d["scenario"], held))
    train = np.flatnonzero(~np.isin(d["scenario"], held))
    mean, std = x[train].mean(axis=0), np.maximum(x[train].std(axis=0), 1e-3)
    print(f"{len(d['people'])} recorded years from {len(scenarios)} scenarios: {len(train)} to train on, "
          f"{len(test)} held out ({len(held)} scenarios); {x.shape[1]} features; on {device}")

    tensor = lambda a: torch.tensor(a, dtype=torch.float32, device=device)
    X, H, B, W = tensor((x - mean) / std), tensor(t_hours), tensor(t_births), tensor(w)
    net = Net(x.shape[1], args.width, args.depth).to(device)
    print(f"{sum(p.numel() for p in net.parameters()):,} parameters")
    opt = torch.optim.Adam(net.parameters(), lr=args.lr)
    steps = args.epochs * ((len(train) + args.batch - 1) // args.batch)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, total_steps=steps)
    tr, te = torch.tensor(train, device=device), torch.tensor(test, device=device)
    started = time.time()
    for epoch in range(args.epochs):
        net.train()
        for b in tr[torch.randperm(len(tr), device=device)].split(args.batch):
            loss, _ = loss_fn(*net(X[b]), H[b], B[b], W[b], args.birth_weight)
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
        if epoch % 10 == 9 or epoch == args.epochs - 1:
            net.eval()
            with torch.no_grad():
                l_tr, m_tr = loss_fn(*net(X[tr]), H[tr], B[tr], W[tr], args.birth_weight)
                l_te, m_te = loss_fn(*net(X[te]), H[te], B[te], W[te], args.birth_weight)
            print(f"epoch {epoch + 1}: loss {l_tr:.4f} train, {l_te:.4f} held out; hours RMS error "
                  f"{m_tr.sqrt():.3f} train, {m_te.sqrt():.3f} held out ({time.time() - started:.0f}s)")

    weights = {"mean": mean, "std": std}
    for k, layer in enumerate(net.layers):
        weights[f"W{k}"] = layer.weight.detach().cpu().numpy().T.astype(np.float64)
        weights[f"b{k}"] = layer.bias.detach().cpu().numpy().astype(np.float64)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    np.savez(args.out, **weights)
    # The NumPy forward pass the game would run must agree with PyTorch's.
    net_np = decisions.load_network(args.out)
    chosen = decisions.network_decide(situation, net_np)
    decisions.check(chosen, situation)
    with torch.no_grad():
        h_t, b_t = (v.cpu().numpy() for v in net(X[te]))
    rows = np.arange(len(test))
    h_np = np.stack([chosen.hours[test][:, :, ACTIVITIES.index("sleep")], chosen.hours[test][rows, :, d["livelihood"][test]],
                     chosen.hours[test][:, :, ACTIVITIES.index("childcare")]], axis=2)
    over = h_t.sum(axis=2) > 24
    gap = max(np.abs(h_np - h_t)[~over].max(initial=0), np.abs(chosen.birth_rate[test] - b_t).max())
    print(f"saved {args.out}; NumPy and PyTorch agree to {gap:.1e}")
    report(d, test, chosen, decisions.decide(situation, engine.load_demography()))


if __name__ == "__main__":
    sys.exit(main())
