import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import './App.css'

const GITHUB_URL = 'https://github.com/'
const pct = (x, sign = true) => (x == null ? '–' : `${sign && x > 0 ? '+' : ''}${(x * 100).toFixed(0)}%`)
const num = (x) => (x == null ? '–' : Math.round(x).toLocaleString('en-US'))
const money = (x) => (x == null ? '–' : `${x < 0 ? '−' : ''}$${Math.abs(Math.round(x)).toLocaleString('en-US')}`)
const tone = (v) => (v === 'worked' ? 'pos' : v === 'lost money' ? 'neg' : 'neu')
const signTone = (s) => (String(s).trim().startsWith('-') ? 'neg' : String(s).trim().startsWith('+') ? 'pos' : '')
const AXIS = { fontSize: 11, fill: 'var(--ink-3)', fontFamily: 'var(--mono)' }
const TIP = { background: 'var(--panel)', border: '1px solid var(--rule)', borderRadius: 6, fontSize: 12 }

export default function App() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState('')
  const [errors, setErrors] = useState([])
  const [margin, setMargin] = useState(40)
  const [file, setFile] = useState(null)
  const [selected, setSelected] = useState(null)
  const [readout, setReadout] = useState(null)
  const [aiLoading, setAiLoading] = useState(false)
  const chartRef = useRef(null)

  async function run(kind, f = file) {
    setErrors([]); setLoading(kind === 'sample' ? 'Analysing the sample data…' : 'Analysing your file…')
    try {
      let res
      if (kind === 'sample') {
        res = await fetch(`/api/sample?margin_pct=${margin / 100}`)
      } else {
        const fd = new FormData(); fd.append('file', f); fd.append('margin_pct', String(margin / 100))
        res = await fetch('/api/analyze', { method: 'POST', body: fd })
      }
      const body = await res.json()
      if (!res.ok) { setErrors(body.errors || [body.detail || 'Something went wrong']) }
      else { setData(body); setReadout(body.readout); setSelected(body.events[0]?.event_id); aiReadout(body, kind) }
    } catch (e) { setErrors([String(e)]) }
    setLoading('')
  }

  async function aiReadout(d, kind) {
    setAiLoading(true)
    try {
      const { readout: _r, ...result } = d
      const res = await fetch('/api/readout', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ result, cache_key: kind === 'sample' ? `sample-${margin}` : null }),
      })
      const out = await res.json()
      if (out.source === 'llm') setReadout(out)
    } catch (e) { /* keep the standard summary */ }
    setAiLoading(false)
  }

  useEffect(() => { run('sample') }, [])  // the product is the preview: load sample data on arrival

  function show(id) {
    setSelected(id)
    chartRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  return (
    <>
      <header className="nav">
        <div className="brand"><span className="mark" />Promo Lift</div>
        <nav>
          <a href="#summary" className="hide-sm">Findings</a>
          <a href="#promotions" className="hide-sm">Promotions</a>
          <a href="#method">Method</a>
          <a href={GITHUB_URL} target="_blank" rel="noreferrer">GitHub</a>
        </nav>
      </header>

      <main className="page">
        <section className="hero">
          <div>
            <p className="eyebrow">Trade promotion analysis</p>
            <h1>Which promotions <em>actually</em> paid off?</h1>
          </div>
          <div>
            <p className="lede">Upload weekly sales and promotion data. It separates real extra sales from sales that were only
              borrowed from sister products or from next week, works out what each promotion earned, and says what to run next.</p>
            <div className="controls">
              <label className="btn primary upload">
                <input type="file" accept=".csv,.xlsx,.xls" onChange={(e) => { const f = e.target.files[0]; setFile(f); if (f) run('upload', f) }} />
                Upload CSV or Excel
              </label>
              <button className="btn" onClick={() => run('sample')} disabled={!!loading}>Reload sample</button>
            </div>
            <label className="margin">Brand gross margin
              <input type="number" min="5" max="90" value={margin} onChange={(e) => setMargin(+e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && run(data?.dataset === 'sample' || !file ? 'sample' : 'upload')} />%
              <button className="btn" style={{ padding: '8px 12px' }} onClick={() => run(data?.dataset === 'sample' || !file ? 'sample' : 'upload')}>Apply</button>
            </label>
            <p className="under">No sign-up. Your file is analysed in memory and not stored. <a href="/api/sample.csv" download="sample_promo_data.csv">Download the template</a>.</p>
            {loading && <div className="status">{loading}</div>}
            {errors.length > 0 && <div className="errors"><b>Please fix the file</b><ul>{errors.map((e) => <li key={e}>{e}</li>)}</ul>
              <small>Required columns: week, store_id, product_id, product_name, category, units, price, base_price, on_promo. Optional: display, feature.</small></div>}
          </div>
        </section>

        {data && <Results data={data} readout={readout} aiLoading={aiLoading} selected={selected} setSelected={setSelected} show={show} chartRef={chartRef} />}

        <Method />
        <footer>
          <span>Built by Ravikoti Deekshith · Python, FastAPI, pandas, React</span>
          <span>Sample data is synthetic, modelled on the structure of dunnhumby's “Breakfast at the Frat”.</span>
        </footer>
      </main>
    </>
  )
}

function Results({ data, readout, aiLoading, selected, setSelected, show, chartRef }) {
  const k = data.kpis
  const ev = data.events.find((e) => e.event_id === selected) || data.events[0]
  return (
    <div className="fade" key={data.dataset + k.margin_pct}>
      <section className="section">
        <div className="section-head">
          <h2>At a glance</h2>
          <span className="meta">{data.dataset === 'sample'
            ? `Sample · ${k.products} products · ${k.stores} stores · ${k.weeks} weeks · synthetic`
            : `${data.dataset} · ${k.products} products · ${k.stores} stores · ${k.weeks} weeks`} · margin {Math.round(k.margin_pct * 100)}%</span>
        </div>
        <div className="kpis">
          <Kpi label="Promotions analysed" value={k.events} />
          <Kpi label="Paid for themselves" value={pct(k.paid_back_pct, false)} tone={k.paid_back_pct >= 0.5 ? 'pos' : 'neg'} />
          <Kpi label="Return on discount spend" value={pct(k.overall_roi)} tone={k.overall_roi >= 0 ? 'pos' : 'neg'} />
          <Kpi label="Extra sales only borrowed" value={pct(k.leakage_pct, false)} tone="neg" />
          <Kpi label="Real extra units" value={num(k.net_incremental_units)} sub={`of ${num(k.gross_lift_units)} during promos`} />
          <Kpi label="Profit after discounts" value={money(k.profit)} tone={k.profit >= 0 ? 'pos' : 'neg'} sub={`${money(k.promo_cost)} spent`} />
        </div>
      </section>

      <section className="section" id="summary">
        <div className="section-head">
          <h2>What this means</h2>
          <span className="tag">{readout?.source === 'llm' ? 'AI summary' : aiLoading ? 'Writing AI summary…' : 'Summary'}</span>
        </div>
        {readout && <>
          <p className="headline">{readout.headline}</p>
          <p className="summary">{readout.summary}</p>
          <div className="recs">{readout.recommendations?.map((r, i) => (
            <div className="rec" key={i}>
              <span className="act">{r.action}</span>
              <h3>{r.title}</h3>
              <p>{r.why}</p>
              {r.metrics?.length > 0 && <div className="metrics">{r.metrics.map((m) => (
                <div className="metric" key={m.label}><b className={signTone(m.value)}>{m.value}</b><span>{m.label}</span></div>))}</div>}
              {r.events?.length > 0 && <div className="events">See {r.events.map((id) => (
                <button key={id} className="ev-link" onClick={() => show(id)}>{id}</button>))}</div>}
            </div>))}</div>
          <p className="note">Every number here comes from the analysis. The summary is checked so nothing is made up.</p>
        </>}
      </section>

      <section className="section">
        <div className="section-head"><h2>Where the money went</h2></div>
        <div className="grid2">
          <div className="panel" ref={chartRef}>
            <h3>Where the extra sales went · {ev.event_id}</h3>
            <p className="sub">{ev.product_name} · week {ev.start} · {ev.mechanic.toLowerCase()} · {pct(ev.depth, false)} off · {ev.stores} stores</p>
            <Waterfall ev={ev} />
            <p className="foot">Return <b className={`num ${tone(ev.verdict)}`}>{pct(ev.roi)}</b> · margin on {num(ev.net_incremental)} real extra units against {money(ev.promo_cost)} of discount.</p>
          </div>
          <div className="panel"><h3>Return by promotion type</h3><p className="sub">Profit for every $1 spent on discounts</p>
            <GroupBars rows={data.by_mechanic} k="mechanic" /></div>
          <div className="panel"><h3>Return by discount size</h3><p className="sub">Bigger discounts sell more but rarely pay for themselves</p>
            <GroupBars rows={data.by_depth} k="depth_band" /></div>
          <div className="panel"><h3>Borrowed sales by category</h3><p className="sub">Share of extra sales taken from sister products or later weeks</p>
            <LeakBars rows={data.by_category} /></div>
        </div>
      </section>

      <section className="section" id="promotions">
        <div className="section-head"><h2>Every promotion</h2><span className="meta">Best return first · select a row to see its breakdown</span></div>
        <div className="table-wrap"><table>
          <thead><tr><th>ID</th><th>Product</th><th className="r">Week</th><th>Promotion</th><th className="r">Off</th><th className="r">Normal</th><th className="r">Boost</th><th className="r">From sisters</th><th className="r">Dip after</th><th className="r">Real extra</th><th className="r">Return</th><th>Result</th></tr></thead>
          <tbody>{data.events.map((e) => (
            <tr key={e.event_id} className={e.event_id === ev.event_id ? 'sel' : ''} onClick={() => show(e.event_id)}>
              <td className="num">{e.event_id}</td><td>{e.product_name}</td><td className="r">{e.start}</td><td>{e.mechanic}</td><td className="r">{pct(e.depth, false)}</td>
              <td className="r">{num(e.baseline)}</td><td className="r">{num(e.gross_lift)}</td><td className="r">{num(e.cannibalized)}</td>
              <td className="r">{num(e.dip)}</td><td className="r">{num(e.net_incremental)}</td>
              <td className={`r ${tone(e.verdict)}`}>{pct(e.roi)}</td>
              <td className={tone(e.verdict)}>{e.verdict}</td></tr>))}</tbody>
        </table></div>
      </section>
    </div>
  )
}

function Kpi({ label, value, sub, tone: t }) {
  return <div className={`kpi ${t || ''}`}><div className="kpi-v">{value}</div><div className="kpi-l">{label}</div>{sub && <div className="kpi-s">{sub}</div>}</div>
}

function Waterfall({ ev }) {
  const rows = useMemo(() => {
    const b = ev.baseline, g = ev.gross_lift, c = ev.cannibalized, d = ev.dip, n = ev.net_incremental
    return [
      { name: 'Normal', range: [0, b], fill: 'var(--bar)', size: b },
      { name: '+ Boost', range: [b, b + g], fill: 'var(--pos)', size: g },
      { name: '− Sisters', range: [b + g - c, b + g], fill: 'var(--neg)', size: c },
      { name: '− Dip after', range: [b + g - c - d, b + g - c], fill: 'var(--neg)', size: d },
      { name: '= Real extra', range: [b, b + n], fill: 'var(--accent)', size: n },
    ].map((r) => ({ ...r, range: [Math.min(...r.range), Math.max(...r.range)] }))
  }, [ev])
  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={rows} margin={{ top: 8, right: 4, left: 0, bottom: 0 }}>
        <CartesianGrid vertical={false} stroke="var(--rule)" />
        <XAxis dataKey="name" tick={AXIS} interval={0} axisLine={false} tickLine={false} />
        <YAxis tick={AXIS} tickFormatter={num} width={48} axisLine={false} tickLine={false} />
        <Tooltip cursor={{ fill: 'transparent' }} formatter={(v, n, p) => [num(Math.abs(p.payload.size)), 'units']} contentStyle={TIP} />
        <Bar dataKey="range" radius={2} maxBarSize={56}>{rows.map((r) => <Cell key={r.name} fill={r.fill} />)}</Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

function GroupBars({ rows, k }) {
  const d = rows.map((r) => ({ name: r[k], roi: Math.round((r.roi ?? 0) * 100) }))
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={d} layout="vertical" margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid horizontal={false} stroke="var(--rule)" />
        <XAxis type="number" tickFormatter={(v) => `${v}%`} tick={AXIS} axisLine={false} tickLine={false} />
        <YAxis type="category" dataKey="name" width={150} tick={{ ...AXIS, fontFamily: 'var(--sans)', fill: 'var(--ink-2)' }} axisLine={false} tickLine={false} />
        <ReferenceLine x={0} stroke="var(--ink-3)" />
        <Tooltip cursor={{ fill: 'transparent' }} formatter={(v) => [`${v}%`, 'Return']} contentStyle={TIP} />
        <Bar dataKey="roi" radius={2} maxBarSize={22}>{d.map((r) => <Cell key={r.name} fill={r.roi >= 0 ? 'var(--pos)' : 'var(--neg)'} />)}</Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

function LeakBars({ rows }) {
  const d = rows.map((r) => ({ name: r.category, leak: Math.round((r.leakage_pct ?? 0) * 100) })).sort((a, b) => b.leak - a.leak)
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={d} layout="vertical" margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid horizontal={false} stroke="var(--rule)" />
        <XAxis type="number" domain={[0, 100]} tickFormatter={(v) => `${v}%`} tick={AXIS} axisLine={false} tickLine={false} />
        <YAxis type="category" dataKey="name" width={150} tick={{ ...AXIS, fontFamily: 'var(--sans)', fill: 'var(--ink-2)' }} axisLine={false} tickLine={false} />
        <Tooltip cursor={{ fill: 'transparent' }} formatter={(v) => [`${v}%`, 'Borrowed']} contentStyle={TIP} />
        <Bar dataKey="leak" fill="var(--neu)" radius={2} maxBarSize={22} />
      </BarChart>
    </ResponsiveContainer>
  )
}

function Method() {
  return (
    <section className="section method" id="method">
      <div><h2>How the numbers are worked out</h2></div>
      <ol>
        <li><span><b>Normal sales.</b> What each product would have sold in each store without a promotion: the typical sales of nearby weeks with no promotion.</span></li>
        <li><span><b>Boost</b> = what actually sold during the promotion − normal sales.</span></li>
        <li><span><b>From sisters.</b> Sales lost by other products in the same category and store while the promotion ran. Those shoppers would have bought from the brand anyway.</span></li>
        <li><span><b>Dip after.</b> Sales below normal in the 2 weeks after, because shoppers stocked up.</span></li>
        <li><span><b>Real extra</b> = boost − from sisters − dip after.</span></li>
        <li><span><b>Return</b> = (margin on real extra sales − cost of the discount) ÷ cost of the discount. The discount cost counts every unit sold at the lower price.</span></li>
        <li><span><b>Summary.</b> All numbers are calculated first. An AI model then writes the plain-English summary, and any number it uses that isn't in the analysis is rejected.</span></li>
      </ol>
    </section>
  )
}
