import { useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const START_EQUITY = 100000;

async function getJSON(path) {
  const res = await fetch(`${path}?t=${Date.now()}`);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

const money = (n) =>
  n == null ? "—" : n.toLocaleString("fr-FR", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const pct = (n) => (n == null ? "—" : `${n >= 0 ? "+" : ""}${n.toFixed(2)} %`);
const cls = (n) => (n > 0 ? "pos" : n < 0 ? "neg" : "flat");

function Kpi({ label, value, tone }) {
  return (
    <div className="card kpi">
      <div className="label">{label}</div>
      <div className={`value ${tone || ""}`}>{value}</div>
    </div>
  );
}

export default function App() {
  const [live, setLive] = useState(null);
  const [bt, setBt] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let alive = true;
    const load = () => {
      getJSON("/data/live.json").then((d) => alive && setLive(d)).catch((e) => alive && setErr(String(e)));
      getJSON("/data/history.json").then((d) => alive && setBt(d)).catch(() => {});
    };
    load();
    const id = setInterval(load, 60000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const latest = live?.latest;
  const equity = latest?.equity;
  const pnl = equity != null ? equity - START_EQUITY : null;
  const pnlPct = pnl != null ? (pnl / START_EQUITY) * 100 : null;

  const liveCurve = useMemo(
    () =>
      (live?.equity_curve || []).map((r) => ({
        t: new Date(r.ts).toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }),
        equity: r.equity,
      })),
    [live]
  );
  const btCurve = useMemo(
    () => (bt?.equity_curve || []).map((r) => ({ t: r.date, equity: r.equity })),
    [bt]
  );

  const decisions = useMemo(() => (live?.decisions || []).slice(-40).reverse(), [live]);
  const positions = latest?.positions || [];
  const m = bt?.metrics || {};

  return (
    <div className="wrap">
      <header className="top">
        <h1>Agent Alpaca — Spreads à crédit / Régime</h1>
        {latest && (
          <span className={`badge ${latest.dry_run ? "sim" : "live"}`}>
            {latest.dry_run ? "simulation" : "ordres réels"}
          </span>
        )}
      </header>
      <div className="sub">
        Régime MM10/MM30 → bull put / bear call / iron condor · garde-fou déterministe ·{" "}
        {latest ? `maj ${new Date(latest.ts).toLocaleString("fr-FR")}` : "chargement…"}
        {err && <span className="err"> · {err}</span>}
      </div>

      <div className="grid kpis">
        <Kpi label="Equity (live)" value={money(equity)} />
        <Kpi label="P&L depuis 100k$" value={pnl == null ? "—" : `${pnl >= 0 ? "+" : ""}${money(pnl)}`} tone={cls(pnl || 0)} />
        <Kpi label="P&L %" value={pct(pnlPct)} tone={cls(pnlPct || 0)} />
        <Kpi label="Positions ouvertes" value={latest?.open_positions ?? "—"} />
        <Kpi label="Backtest 17 mois" value={pct(m.total_return_pct)} tone={cls(m.total_return_pct || 0)} />
        <Kpi label="Max drawdown (bt)" value={pct(m.max_drawdown_pct)} tone="neg" />
        <Kpi label="Sharpe (bt)" value={m.sharpe_annualized ?? "—"} />
        <Kpi label="Win rate (bt)" value={m.win_rate_pct != null ? `${m.win_rate_pct} %` : "—"} />
      </div>

      <div className="grid cols-2">
        <div className="card">
          <h2>Courbe d'equity — live</h2>
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={liveCurve} margin={{ left: 8, right: 8, top: 6 }}>
              <defs>
                <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#58a6ff" stopOpacity={0.35} />
                  <stop offset="100%" stopColor="#58a6ff" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="#222e40" vertical={false} />
              <XAxis dataKey="t" tick={{ fill: "#8a99ad", fontSize: 11 }} minTickGap={40} />
              <YAxis
                domain={([min, max]) => (max - min < 400 ? [min - 400, max + 400] : ["auto", "auto"])}
                tick={{ fill: "#8a99ad", fontSize: 11 }}
                width={78}
                tickFormatter={(v) => `$${Math.round(v).toLocaleString("en-US")}`}
              />
              <Tooltip contentStyle={{ background: "#131a26", border: "1px solid #222e40" }}
                formatter={(v) => money(v)} />
              <Area type="monotone" dataKey="equity" stroke="#58a6ff" fill="url(#g)" strokeWidth={2} isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        <div className="card">
          <h2>Courbe d'equity — backtest de référence</h2>
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={btCurve} margin={{ left: 8, right: 8, top: 6 }}>
              <CartesianGrid stroke="#222e40" vertical={false} />
              <XAxis dataKey="t" tick={{ fill: "#8a99ad", fontSize: 11 }} minTickGap={60} />
              <YAxis domain={["auto", "auto"]} tick={{ fill: "#8a99ad", fontSize: 11 }} width={70}
                tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`} />
              <Tooltip contentStyle={{ background: "#131a26", border: "1px solid #222e40" }}
                formatter={(v) => money(v)} />
              <Line type="monotone" dataKey="equity" stroke="#3fb950" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="grid cols-2" style={{ marginTop: 16 }}>
        <div className="card">
          <h2>Régimes de marché</h2>
          <div className="chips">
            {latest &&
              Object.entries(latest.regimes || {}).map(([s, r]) => (
                <span key={s} className={`chip ${r}`}>
                  {s} · {r}
                </span>
              ))}
          </div>

          <h2 style={{ marginTop: 20 }}>Positions ouvertes</h2>
          {positions.length === 0 ? (
            <div className="flat">aucune</div>
          ) : (
            <table>
              <thead>
                <tr><th>Sous-jacent</th><th>Structure</th><th className="num">Crédit</th><th className="num">P&L latent</th><th className="num">DTE</th></tr>
              </thead>
              <tbody>
                {positions.map((p, i) => (
                  <tr key={i}>
                    <td>{p.symbol}</td>
                    <td>{p.kind}</td>
                    <td className="num">${p.entry_credit}</td>
                    <td className={`num ${cls(p.unrealized_pl)}`}>{p.unrealized_pl >= 0 ? "+" : ""}${p.unrealized_pl}</td>
                    <td className="num">{p.dte}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card">
          <h2>Journal de décisions</h2>
          <div className="scroll">
            <table>
              <thead>
                <tr><th>Heure</th><th>Sym.</th><th>Action</th><th>Raison</th></tr>
              </thead>
              <tbody>
                {decisions.map((d, i) => (
                  <tr key={i}>
                    <td>{new Date(d.ts).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}</td>
                    <td>{d.symbol}</td>
                    <td><span className={`act ${d.action}`}>{d.action}</span></td>
                    <td>{d.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Backtest — derniers trades clôturés</h2>
        <div className="scroll">
          <table>
            <thead>
              <tr><th>Ouvert</th><th>Clôturé</th><th>Sym.</th><th>Structure</th><th className="num">P&L</th><th>Motif de clôture</th></tr>
            </thead>
            <tbody>
              {(bt?.trades || []).slice(-30).reverse().map((t, i) => (
                <tr key={i}>
                  <td>{t.open_date}</td>
                  <td>{t.close_date}</td>
                  <td>{t.symbol}</td>
                  <td>{t.kind}</td>
                  <td className={`num ${cls(t.pl)}`}>{t.pl >= 0 ? "+" : ""}${t.pl}</td>
                  <td>{t.close_reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="foot">
        Alpaca AI Trading Agents Hackathon · lablab.ai × Alpaca · compte paper ·
        données de marché réelles, capital virtuel.
      </div>
    </div>
  );
}
