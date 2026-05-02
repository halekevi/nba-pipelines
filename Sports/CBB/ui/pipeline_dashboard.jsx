import { useState, useMemo } from "react";

// ── Pipeline Stats ─────────────────────────────────────────────────────────────
const STATS = {
  step1: { rows: 6227, players: 182, teams: 26, standard: 1145, goblin: 1964, demon: 3118 },
  step2: { rows: 6227, singles: 6139, combos: 88, resolved: 6227, standard: 1145, goblin: 1964, demon: 3118 },
};

// ── Embedded Sample Data (Step2 singles) ───────────────────────────────────────
const RAW_DATA = [
  {player:"Jarace Walker",pos:"F",team:"IND",opp_team:"WAS",line:"39.5",prop_type:"Pts+Rebs",prop_norm:"pr",pick_type:"Demon",nba_player_id:"1641716"},
  {player:"Jarace Walker",pos:"F",team:"IND",opp_team:"WAS",line:"35.5",prop_type:"Pts+Rebs+Asts",prop_norm:"pra",pick_type:"Demon",nba_player_id:"1641716"},
  {player:"Jarace Walker",pos:"F",team:"IND",opp_team:"WAS",line:"35.5",prop_type:"Fantasy Score",prop_norm:"fantasy",pick_type:"Standard",nba_player_id:"1641716"},
  {player:"Jarace Walker",pos:"F",team:"IND",opp_team:"WAS",line:"34.5",prop_type:"Pts+Rebs",prop_norm:"pr",pick_type:"Demon",nba_player_id:"1641716"},
  {player:"Jarace Walker",pos:"F",team:"IND",opp_team:"WAS",line:"33.5",prop_type:"Pts+Rebs+Asts",prop_norm:"pra",pick_type:"Demon",nba_player_id:"1641716"},
  {player:"Jarace Walker",pos:"F",team:"IND",opp_team:"WAS",line:"30.5",prop_type:"Pts+Rebs+Asts",prop_norm:"pra",pick_type:"Standard",nba_player_id:"1641716"},
  {player:"Jarace Walker",pos:"F",team:"IND",opp_team:"WAS",line:"29.5",prop_type:"Pts+Rebs+Asts",prop_norm:"pra",pick_type:"Goblin",nba_player_id:"1641716"},
  {player:"Jarace Walker",pos:"F",team:"IND",opp_team:"WAS",line:"27.5",prop_type:"Pts+Rebs+Asts",prop_norm:"pra",pick_type:"Goblin",nba_player_id:"1641716"},
  {player:"Jarace Walker",pos:"F",team:"IND",opp_team:"WAS",line:"27.0",prop_type:"Pts+Rebs",prop_norm:"pr",pick_type:"Standard",nba_player_id:"1641716"},
  {player:"Andrew Nembhard",pos:"G",team:"IND",opp_team:"WAS",line:"31.5",prop_type:"Pts+Rebs+Asts",prop_norm:"pra",pick_type:"Demon",nba_player_id:"1630549"},
  {player:"Andrew Nembhard",pos:"G",team:"IND",opp_team:"WAS",line:"29.5",prop_type:"Pts+Rebs+Asts",prop_norm:"pra",pick_type:"Demon",nba_player_id:"1630549"},
  {player:"Andrew Nembhard",pos:"G",team:"IND",opp_team:"WAS",line:"29.5",prop_type:"Pts+Asts",prop_norm:"pa",pick_type:"Demon",nba_player_id:"1630549"},
  {player:"Andrew Nembhard",pos:"G",team:"IND",opp_team:"WAS",line:"26.5",prop_type:"Pts+Rebs+Asts",prop_norm:"pra",pick_type:"Standard",nba_player_id:"1630549"},
  {player:"Andrew Nembhard",pos:"G",team:"IND",opp_team:"WAS",line:"24.5",prop_type:"Pts+Rebs+Asts",prop_norm:"pra",pick_type:"Goblin",nba_player_id:"1630549"},
  {player:"Kobe Brown",pos:"F",team:"IND",opp_team:"WAS",line:"27.5",prop_type:"Pts+Rebs+Asts",prop_norm:"pra",pick_type:"Demon",nba_player_id:"1631218"},
  {player:"Kobe Brown",pos:"F",team:"IND",opp_team:"WAS",line:"23.0",prop_type:"Pts+Rebs+Asts",prop_norm:"pra",pick_type:"Standard",nba_player_id:"1631218"},
  {player:"Kobe Brown",pos:"F",team:"IND",opp_team:"WAS",line:"19.5",prop_type:"Pts+Rebs+Asts",prop_norm:"pra",pick_type:"Goblin",nba_player_id:"1631218"},
  {player:"Kobe Brown",pos:"F",team:"IND",opp_team:"WAS",line:"12.5",prop_type:"Points",prop_norm:"pts",pick_type:"Standard",nba_player_id:"1631218"},
  {player:"Kobe Brown",pos:"F",team:"IND",opp_team:"WAS",line:"8.5",prop_type:"Points",prop_norm:"pts",pick_type:"Goblin",nba_player_id:"1631218"},
  {player:"AJ Green",pos:"G",team:"IND",opp_team:"WAS",line:"12.0",prop_type:"Points",prop_norm:"pts",pick_type:"Standard",nba_player_id:"1631260"},
  {player:"AJ Green",pos:"G",team:"IND",opp_team:"WAS",line:"19.5",prop_type:"Points",prop_norm:"pts",pick_type:"Demon",nba_player_id:"1631260"},
  {player:"AJ Green",pos:"G",team:"IND",opp_team:"WAS",line:"14.5",prop_type:"Points",prop_norm:"pts",pick_type:"Demon",nba_player_id:"1631260"},
  {player:"AJ Green",pos:"G",team:"IND",opp_team:"WAS",line:"7.5",prop_type:"Points",prop_norm:"pts",pick_type:"Goblin",nba_player_id:"1631260"},
  {player:"Tyler Herro",pos:"G",team:"MIA",opp_team:"ATL",line:"22.5",prop_type:"Points",prop_norm:"pts",pick_type:"Standard",nba_player_id:"1629638"},
  {player:"Tyler Herro",pos:"G",team:"MIA",opp_team:"ATL",line:"19.5",prop_type:"Pts+Asts",prop_norm:"pa",pick_type:"Standard",nba_player_id:"1629638"},
  {player:"Tyler Herro",pos:"G",team:"MIA",opp_team:"ATL",line:"24.5",prop_type:"Pts+Asts",prop_norm:"pa",pick_type:"Demon",nba_player_id:"1629638"},
  {player:"Tyler Herro",pos:"G",team:"MIA",opp_team:"ATL",line:"29.5",prop_type:"Pts+Asts",prop_norm:"pa",pick_type:"Demon",nba_player_id:"1629638"},
  {player:"Bam Adebayo",pos:"C",team:"MIA",opp_team:"ATL",line:"21.5",prop_type:"Points",prop_norm:"pts",pick_type:"Standard",nba_player_id:"1628389"},
  {player:"Bam Adebayo",pos:"C",team:"MIA",opp_team:"ATL",line:"28.5",prop_type:"Rebounds",prop_norm:"reb",pick_type:"Standard",nba_player_id:"1628389"},
  {player:"Jalen Johnson",pos:"F",team:"ATL",opp_team:"MIA",line:"23.5",prop_type:"Points",prop_norm:"pts",pick_type:"Standard",nba_player_id:"1630553"},
  {player:"Jalen Johnson",pos:"F",team:"ATL",opp_team:"MIA",line:"50.0",prop_type:"Fantasy Score",prop_norm:"fantasy",pick_type:"Standard",nba_player_id:"1630553"},
  {player:"Jalen Johnson",pos:"F",team:"ATL",opp_team:"MIA",line:"20.5",prop_type:"Points",prop_norm:"pts",pick_type:"Goblin",nba_player_id:"1630553"},
  {player:"Jalen Johnson",pos:"F",team:"ATL",opp_team:"MIA",line:"29.5",prop_type:"Points",prop_norm:"pts",pick_type:"Demon",nba_player_id:"1630553"},
  {player:"Jaylen Wells",pos:"G",team:"MEM",opp_team:"UTA",line:"17.5",prop_type:"Points",prop_norm:"pts",pick_type:"Standard",nba_player_id:"1641754"},
  {player:"Jaylen Wells",pos:"G",team:"MEM",opp_team:"UTA",line:"15.5",prop_type:"Points",prop_norm:"pts",pick_type:"Goblin",nba_player_id:"1641754"},
  {player:"Jaylen Wells",pos:"G",team:"MEM",opp_team:"UTA",line:"19.5",prop_type:"Points",prop_norm:"pts",pick_type:"Demon",nba_player_id:"1641754"},
  {player:"Jaylen Wells",pos:"G",team:"MEM",opp_team:"UTA",line:"19.5",prop_type:"Pts+Asts",prop_norm:"pa",pick_type:"Standard",nba_player_id:"1641754"},
  {player:"Jaylen Wells",pos:"G",team:"MEM",opp_team:"UTA",line:"24.5",prop_type:"Pts+Asts",prop_norm:"pa",pick_type:"Demon",nba_player_id:"1641754"},
  {player:"Jaylen Wells",pos:"G",team:"MEM",opp_team:"UTA",line:"29.5",prop_type:"Pts+Asts",prop_norm:"pa",pick_type:"Demon",nba_player_id:"1641754"},
  {player:"Bilal Coulibaly",pos:"G",team:"WAS",opp_team:"IND",line:"10.5",prop_type:"Points",prop_norm:"pts",pick_type:"Standard",nba_player_id:"1641731"},
  {player:"Bilal Coulibaly",pos:"G",team:"WAS",opp_team:"IND",line:"7.5",prop_type:"Points",prop_norm:"pts",pick_type:"Goblin",nba_player_id:"1641731"},
  {player:"Bilal Coulibaly",pos:"G",team:"WAS",opp_team:"IND",line:"14.5",prop_type:"Points",prop_norm:"pts",pick_type:"Demon",nba_player_id:"1641731"},
  {player:"Kyshawn George",pos:"G",team:"WAS",opp_team:"IND",line:"19.5",prop_type:"Pts+Rebs+Asts",prop_norm:"pra",pick_type:"Standard",nba_player_id:"1641746"},
  {player:"Kyshawn George",pos:"G",team:"WAS",opp_team:"IND",line:"14.5",prop_type:"Pts+Rebs+Asts",prop_norm:"pra",pick_type:"Goblin",nba_player_id:"1641746"},
  {player:"Kyshawn George",pos:"G",team:"WAS",opp_team:"IND",line:"24.5",prop_type:"Pts+Rebs+Asts",prop_norm:"pra",pick_type:"Demon",nba_player_id:"1641746"},
  {player:"LaMelo Ball",pos:"G",team:"CHA",opp_team:"HOU",line:"19.5",prop_type:"Points",prop_norm:"pts",pick_type:"Standard",nba_player_id:"1630173"},
  {player:"LaMelo Ball",pos:"G",team:"CHA",opp_team:"HOU",line:"24.5",prop_type:"Points",prop_norm:"pts",pick_type:"Demon",nba_player_id:"1630173"},
  {player:"LaMelo Ball",pos:"G",team:"CHA",opp_team:"HOU",line:"29.5",prop_type:"Points",prop_norm:"pts",pick_type:"Demon",nba_player_id:"1630173"},
  {player:"Brandon Miller",pos:"F",team:"CHA",opp_team:"HOU",line:"19.5",prop_type:"Points",prop_norm:"pts",pick_type:"Standard",nba_player_id:"1641713"},
  {player:"Dennis Schröder",pos:"G",team:"CLE",opp_team:"CHA",line:"8.5",prop_type:"Points",prop_norm:"pts",pick_type:"Standard",nba_player_id:"203471"},
  {player:"Dennis Schröder",pos:"G",team:"CLE",opp_team:"CHA",line:"4.5",prop_type:"Points",prop_norm:"pts",pick_type:"Goblin",nba_player_id:"203471"},
  {player:"Dennis Schröder",pos:"G",team:"CLE",opp_team:"CHA",line:"9.5",prop_type:"Points",prop_norm:"pts",pick_type:"Demon",nba_player_id:"203471"},
  {player:"Dennis Schröder",pos:"G",team:"CLE",opp_team:"CHA",line:"11.5",prop_type:"Points",prop_norm:"pts",pick_type:"Demon",nba_player_id:"203471"},
  {player:"Dennis Schröder",pos:"G",team:"CLE",opp_team:"CHA",line:"14.5",prop_type:"Points",prop_norm:"pts",pick_type:"Demon",nba_player_id:"203471"},
  {player:"Donovan Mitchell",pos:"G",team:"CLE",opp_team:"CHA",line:"26.5",prop_type:"Points",prop_norm:"pts",pick_type:"Standard",nba_player_id:"1628378"},
  {player:"Donovan Mitchell",pos:"G",team:"CLE",opp_team:"CHA",line:"34.5",prop_type:"Points",prop_norm:"pts",pick_type:"Demon",nba_player_id:"1628378"},
  {player:"Nikola Jokić",pos:"C",team:"DEN",opp_team:"POR",line:"28.5",prop_type:"Points",prop_norm:"pts",pick_type:"Standard",nba_player_id:"203999"},
  {player:"Nikola Jokić",pos:"C",team:"DEN",opp_team:"POR",line:"34.5",prop_type:"Points",prop_norm:"pts",pick_type:"Demon",nba_player_id:"203999"},
  {player:"Nikola Jokić",pos:"C",team:"DEN",opp_team:"POR",line:"39.5",prop_type:"Points",prop_norm:"pts",pick_type:"Demon",nba_player_id:"203999"},
  {player:"Jamal Murray",pos:"G",team:"DEN",opp_team:"POR",line:"25.5",prop_type:"Points",prop_norm:"pts",pick_type:"Standard",nba_player_id:"1627750"},
  {player:"Luka Dončić",pos:"G",team:"LAL",opp_team:"GSW",line:"28.5",prop_type:"Points",prop_norm:"pts",pick_type:"Standard",nba_player_id:"1629029"},
  {player:"LeBron James",pos:"F",team:"LAL",opp_team:"GSW",line:"22.5",prop_type:"Points",prop_norm:"pts",pick_type:"Standard",nba_player_id:"2544"},
  {player:"Anthony Edwards",pos:"G",team:"MIN",opp_team:"DAL",line:"28.5",prop_type:"Points",prop_norm:"pts",pick_type:"Standard",nba_player_id:"1630162"},
  {player:"Anthony Edwards",pos:"G",team:"MIN",opp_team:"DAL",line:"34.5",prop_type:"Points",prop_norm:"pts",pick_type:"Demon",nba_player_id:"1630162"},
  {player:"Julius Randle",pos:"F",team:"MIN",opp_team:"DAL",line:"20.5",prop_type:"Points",prop_norm:"pts",pick_type:"Standard",nba_player_id:"203994"},
];

// ── Helpers ────────────────────────────────────────────────────────────────────
const pickColor = (t) => t === "Goblin" ? "#7c3aed" : t === "Demon" ? "#dc2626" : "#2563eb";
const pickBg   = (t) => t === "Goblin" ? "#7c3aed18" : t === "Demon" ? "#dc262618" : "#2563eb18";
const pickEmoji = (t) => t === "Goblin" ? "👺" : t === "Demon" ? "😈" : "⭐";

const TEAMS = [...new Set(RAW_DATA.map(r => r.team))].sort();
const PROPS = [...new Set(RAW_DATA.map(r => r.prop_type))].sort();

export default function App() {
  const [tab, setTab] = useState("overview");
  const [search, setSearch] = useState("");
  const [filterTeam, setFilterTeam] = useState("ALL");
  const [filterProp, setFilterProp] = useState("ALL");
  const [filterType, setFilterType] = useState("ALL");
  const [sortCol, setSortCol] = useState("player");
  const [sortDir, setSortDir] = useState(1);

  const filtered = useMemo(() => {
    let d = RAW_DATA;
    if (search) d = d.filter(r => r.player.toLowerCase().includes(search.toLowerCase()));
    if (filterTeam !== "ALL") d = d.filter(r => r.team === filterTeam);
    if (filterProp !== "ALL") d = d.filter(r => r.prop_type === filterProp);
    if (filterType !== "ALL") d = d.filter(r => r.pick_type === filterType);
    d = [...d].sort((a, b) => {
      const va = sortCol === "line" ? parseFloat(a[sortCol]) : a[sortCol];
      const vb = sortCol === "line" ? parseFloat(b[sortCol]) : b[sortCol];
      return va < vb ? -sortDir : va > vb ? sortDir : 0;
    });
    return d;
  }, [search, filterTeam, filterProp, filterType, sortCol, sortDir]);

  const toggleSort = (col) => {
    if (sortCol === col) setSortDir(d => -d);
    else { setSortCol(col); setSortDir(1); }
  };

  const s1 = STATS.step1;
  const s2 = STATS.step2;

  return (
    <div style={{minHeight:"100vh",background:"#030712",fontFamily:"'Courier New',monospace",color:"#f1f5f9"}}>
      <style>{`
        * { box-sizing: border-box; }
        ::-webkit-scrollbar { width:5px; height:5px; }
        ::-webkit-scrollbar-track { background:#0a0f1e; }
        ::-webkit-scrollbar-thumb { background:#334155; border-radius:3px; }
        button:hover { opacity:0.8; }
        select option { background:#0f172a; }
        th { cursor:pointer; user-select:none; }
        th:hover { color:#94a3b8 !important; }
        tr:hover td { background:rgba(255,255,255,0.04) !important; }
      `}</style>

      {/* Header */}
      <div style={{background:"#0a0f1e",borderBottom:"1px solid #1e293b",padding:"18px 28px",display:"flex",alignItems:"center",justifyContent:"space-between",flexWrap:"wrap",gap:12}}>
        <div>
          <div style={{fontSize:18,fontWeight:"bold",letterSpacing:1,color:"#f8fafc"}}>
            🏀 NBA PROP PIPELINE
          </div>
          <div style={{fontSize:10,color:"#475569",marginTop:2,letterSpacing:2}}>PRIZEPICKS · STEP1 → STEP2 · NBA PIPELINE A</div>
        </div>
        <div style={{display:"flex",gap:8,flexWrap:"wrap"}}>
          {[
            {label:`${s1.rows.toLocaleString()} ROWS`,color:"#3b82f6"},
            {label:`${s1.players} PLAYERS`,color:"#10b981"},
            {label:`${s1.teams} TEAMS`,color:"#f59e0b"},
            {label:`${s2.combos} COMBOS`,color:"#a78bfa"},
          ].map(({label,color}) => (
            <div key={label} style={{background:`${color}18`,border:`1px solid ${color}44`,borderRadius:6,padding:"4px 10px",fontSize:10,color,fontWeight:"bold",letterSpacing:1}}>{label}</div>
          ))}
        </div>
      </div>

      {/* Tabs */}
      <div style={{display:"flex",borderBottom:"1px solid #1e293b",background:"#070d1a",padding:"0 28px"}}>
        {[["overview","📊  OVERVIEW"],["step1","⚡  STEP 1"],["step2","🔗  STEP 2"],["board","📋  PROP BOARD"]].map(([id,label])=>(
          <button key={id} onClick={()=>setTab(id)} style={{
            background:"none",border:"none",borderBottom:tab===id?"2px solid #3b82f6":"2px solid transparent",
            color:tab===id?"#f8fafc":"#475569",padding:"12px 16px",cursor:"pointer",
            fontSize:11,fontWeight:"bold",letterSpacing:1.5,
          }}>{label}</button>
        ))}
      </div>

      <div style={{maxWidth:1100,margin:"0 auto",padding:"24px 20px"}}>

        {/* ── OVERVIEW TAB ── */}
        {tab==="overview" && (
          <div>
            {/* Pipeline flow */}
            <div style={{display:"flex",alignItems:"center",gap:0,marginBottom:28,overflowX:"auto"}}>
              {[
                {step:"STEP 1",label:"Fetch API",desc:"PrizePicks public API → flat CSV",color:"#3b82f6",icon:"📡",stats:[`${s1.rows.toLocaleString()} rows`,`${s1.players} players`,`${s1.teams} teams`]},
                null,
                {step:"STEP 2",label:"Attach Pick Types",desc:"NBA IDs · opp_team · deviation tiers",color:"#10b981",icon:"🔗",stats:[`${s2.singles.toLocaleString()} singles`,`${s2.combos} combos`,`100% resolved`]},
              ].map((item,i) => item === null ? (
                <div key={i} style={{color:"#334155",fontSize:24,padding:"0 12px",flexShrink:0}}>→</div>
              ) : (
                <div key={i} style={{background:`linear-gradient(135deg, ${item.color}18, ${item.color}08)`,border:`1px solid ${item.color}33`,borderRadius:12,padding:"20px 24px",flex:1,minWidth:200}}>
                  <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:12}}>
                    <span style={{fontSize:22}}>{item.icon}</span>
                    <div>
                      <div style={{fontSize:9,color:item.color,letterSpacing:2,fontWeight:"bold"}}>{item.step}</div>
                      <div style={{fontSize:14,color:"#f8fafc",fontWeight:"bold"}}>{item.label}</div>
                    </div>
                  </div>
                  <div style={{color:"#64748b",fontSize:11,marginBottom:12}}>{item.desc}</div>
                  {item.stats.map(s => (
                    <div key={s} style={{color:"#94a3b8",fontSize:11,marginBottom:4}}>· {s}</div>
                  ))}
                </div>
              ))}
            </div>

            {/* Pick type breakdown */}
            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr 1fr",gap:16,marginBottom:24}}>
              {[
                {type:"Standard",count:s1.standard,color:"#2563eb",emoji:"⭐"},
                {type:"Goblin",count:s1.goblin,color:"#7c3aed",emoji:"👺"},
                {type:"Demon",count:s1.demon,color:"#dc2626",emoji:"😈"},
              ].map(({type,count,color,emoji})=>(
                <div key={type} style={{background:`${color}12`,border:`1px solid ${color}33`,borderRadius:10,padding:"18px 20px",textAlign:"center"}}>
                  <div style={{fontSize:28,marginBottom:6}}>{emoji}</div>
                  <div style={{color,fontSize:28,fontWeight:"bold",fontFamily:"'Courier New',monospace"}}>{count.toLocaleString()}</div>
                  <div style={{color:"#475569",fontSize:10,letterSpacing:2,marginTop:4}}>{type.toUpperCase()} LINES</div>
                  <div style={{color:"#334155",fontSize:10,marginTop:4}}>{((count/s1.rows)*100).toFixed(1)}% of board</div>
                </div>
              ))}
            </div>

            {/* Mini bar chart */}
            <div style={{background:"rgba(255,255,255,0.02)",border:"1px solid #1e293b",borderRadius:10,padding:"16px 20px"}}>
              <div style={{fontSize:10,color:"#475569",letterSpacing:2,marginBottom:14}}>BOARD COMPOSITION</div>
              <div style={{display:"flex",height:24,borderRadius:6,overflow:"hidden",gap:2}}>
                {[
                  {type:"Standard",count:s1.standard,color:"#2563eb"},
                  {type:"Goblin",count:s1.goblin,color:"#7c3aed"},
                  {type:"Demon",count:s1.demon,color:"#dc2626"},
                ].map(({type,count,color})=>(
                  <div key={type} style={{background:color,flex:count,display:"flex",alignItems:"center",justifyContent:"center",fontSize:10,color:"#fff",fontWeight:"bold",minWidth:30}}>
                    {((count/s1.rows)*100).toFixed(0)}%
                  </div>
                ))}
              </div>
              <div style={{display:"flex",gap:16,marginTop:10,flexWrap:"wrap"}}>
                {[["⭐ Standard","#2563eb"],["👺 Goblin","#7c3aed"],["😈 Demon","#dc2626"]].map(([label,color])=>(
                  <div key={label} style={{display:"flex",alignItems:"center",gap:6}}>
                    <div style={{width:10,height:10,borderRadius:2,background:color}}/>
                    <span style={{color:"#64748b",fontSize:11}}>{label}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* ── STEP 1 TAB ── */}
        {tab==="step1" && (
          <div>
            <div style={{fontSize:10,color:"#475569",letterSpacing:3,marginBottom:20}}>STEP 1 · FETCH PRIZEPICKS API</div>
            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16,marginBottom:24}}>
              {[
                {label:"API ENDPOINT",value:"api.prizepicks.com/projections",color:"#3b82f6"},
                {label:"LEAGUE ID",value:"7 (NBA)",color:"#10b981"},
                {label:"GAME MODE",value:"pickem",color:"#f59e0b"},
                {label:"PER PAGE",value:"250",color:"#a78bfa"},
                {label:"MAX PAGES",value:"80",color:"#3b82f6"},
                {label:"SLEEP",value:"1.2s + jitter",color:"#10b981"},
                {label:"429 COOLDOWN",value:"60s + 7s jitter",color:"#f59e0b"},
                {label:"MAX COOLDOWNS",value:"2",color:"#dc2626"},
                {label:"403 RETRIES",value:"3 (exp. backoff)",color:"#a78bfa"},
                {label:"OUTPUT",value:"step1_fetch_prizepicks_api.csv",color:"#10b981"},
              ].map(({label,value,color})=>(
                <div key={label} style={{background:"rgba(255,255,255,0.02)",border:"1px solid #1e293b",borderRadius:8,padding:"12px 16px",display:"flex",justifyContent:"space-between",alignItems:"center",gap:8}}>
                  <span style={{color:"#475569",fontSize:10,letterSpacing:1}}>{label}</span>
                  <span style={{color,fontSize:11,fontWeight:"bold",textAlign:"right"}}>{value}</span>
                </div>
              ))}
            </div>

            <div style={{background:"rgba(255,255,255,0.02)",border:"1px solid #1e293b",borderRadius:10,padding:"16px 20px",marginBottom:16}}>
              <div style={{fontSize:10,color:"#475569",letterSpacing:2,marginBottom:12}}>OUTPUT COLUMNS</div>
              <div style={{display:"flex",gap:8,flexWrap:"wrap"}}>
                {["projection_id","pp_projection_id","player_id","pp_game_id","start_time","player","pos","team","opp_team","pp_home_team","pp_away_team","prop_type","line","pick_type"].map(col=>(
                  <div key={col} style={{background:"#1e293b",borderRadius:5,padding:"3px 10px",fontSize:10,color:"#94a3b8",fontFamily:"monospace"}}>{col}</div>
                ))}
              </div>
            </div>

            <div style={{background:"rgba(255,255,255,0.02)",border:"1px solid #1e293b",borderRadius:10,padding:"16px 20px"}}>
              <div style={{fontSize:10,color:"#475569",letterSpacing:2,marginBottom:12}}>ERROR HANDLING</div>
              {[
                {code:"429",action:"Cooldown 60s + jitter → retry same page. Stop after 2 cooldowns.",color:"#f59e0b"},
                {code:"403",action:"Rotate User-Agent → exponential backoff → retry up to 3×.",color:"#dc2626"},
                {code:"5xx",action:"Retry with backoff up to 8 attempts.",color:"#a78bfa"},
                {code:"DUPE",action:"Hard dedupe by projection_id as safety belt.",color:"#10b981"},
                {code:"SMALL",action:"Board guard: min 120 rows + 6 teams required.",color:"#3b82f6"},
              ].map(({code,action,color})=>(
                <div key={code} style={{display:"flex",gap:12,marginBottom:10,alignItems:"flex-start"}}>
                  <div style={{background:`${color}22`,border:`1px solid ${color}44`,borderRadius:4,padding:"2px 8px",fontSize:10,color,fontWeight:"bold",flexShrink:0}}>{code}</div>
                  <div style={{color:"#64748b",fontSize:11}}>{action}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── STEP 2 TAB ── */}
        {tab==="step2" && (
          <div>
            <div style={{fontSize:10,color:"#475569",letterSpacing:3,marginBottom:20}}>STEP 2 · ATTACH PICK TYPES + IDs</div>
            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16,marginBottom:24}}>
              {[
                {label:"INPUT",value:"step1_fetch_prizepicks_api.csv",color:"#3b82f6"},
                {label:"OUTPUT",value:"step2_attach_picktypes.csv",color:"#10b981"},
                {label:"NBA API",value:"nba_api.stats.static.players",color:"#f59e0b"},
                {label:"SINGLES",value:s2.singles.toLocaleString(),color:"#10b981"},
                {label:"COMBOS",value:s2.combos,color:"#a78bfa"},
                {label:"ID RESOLVED",value:"100% (6,227 / 6,227)",color:"#10b981"},
              ].map(({label,value,color})=>(
                <div key={label} style={{background:"rgba(255,255,255,0.02)",border:"1px solid #1e293b",borderRadius:8,padding:"12px 16px",display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                  <span style={{color:"#475569",fontSize:10,letterSpacing:1}}>{label}</span>
                  <span style={{color,fontSize:11,fontWeight:"bold"}}>{value}</span>
                </div>
              ))}
            </div>

            <div style={{background:"rgba(255,255,255,0.02)",border:"1px solid #1e293b",borderRadius:10,padding:"16px 20px",marginBottom:16}}>
              <div style={{fontSize:10,color:"#475569",letterSpacing:2,marginBottom:12}}>ADDED COLUMNS</div>
              <div style={{display:"flex",gap:8,flexWrap:"wrap"}}>
                {["nba_player_id","prop_norm","opp_team","standard_line","deviation_level","is_combo_player","player_1","player_2","team_1","team_2","id_status"].map(col=>(
                  <div key={col} style={{background:"#10b98122",border:"1px solid #10b98133",borderRadius:5,padding:"3px 10px",fontSize:10,color:"#10b981",fontFamily:"monospace"}}>{col}</div>
                ))}
              </div>
            </div>

            <div style={{background:"rgba(255,255,255,0.02)",border:"1px solid #1e293b",borderRadius:10,padding:"16px 20px",marginBottom:16}}>
              <div style={{fontSize:10,color:"#475569",letterSpacing:2,marginBottom:12}}>DEVIATION LEVEL LOGIC</div>
              {[
                {level:"0",desc:"Standard line — baseline reference",color:"#2563eb"},
                {level:"1",desc:"Closest goblin/demon to standard (tier 1)",color:"#10b981"},
                {level:"2",desc:"Second option away from standard (tier 2)",color:"#f59e0b"},
                {level:"3+",desc:"Further out options (tier 3, 4, ...)",color:"#dc2626"},
              ].map(({level,desc,color})=>(
                <div key={level} style={{display:"flex",gap:12,marginBottom:10,alignItems:"center"}}>
                  <div style={{background:`${color}22`,border:`1px solid ${color}44`,borderRadius:4,padding:"2px 10px",fontSize:11,color,fontWeight:"bold",flexShrink:0,minWidth:36,textAlign:"center"}}>{level}</div>
                  <div style={{color:"#64748b",fontSize:11}}>{desc}</div>
                </div>
              ))}
              <div style={{color:"#334155",fontSize:10,marginTop:8,borderTop:"1px solid #1e293b",paddingTop:8}}>
                Rank-based: position in sorted list from standard, not percentage-based. Varies per player+prop.
              </div>
            </div>

            <div style={{background:"rgba(255,255,255,0.02)",border:"1px solid #1e293b",borderRadius:10,padding:"16px 20px"}}>
              <div style={{fontSize:10,color:"#475569",letterSpacing:2,marginBottom:12}}>NAME RESOLUTION STRATEGY</div>
              {[
                {method:"name_strict",desc:"Exact normalized name match (accents stripped, lowercased)"},
                {method:"name_loose",desc:"Loose match ignoring Jr/Sr/II/III suffixes"},
                {method:"name_loose_active_tiebreak",desc:"Multiple matches → filter by is_active=True"},
                {method:"UNRESOLVED_SINGLE",desc:"No match found — flagged in id_status"},
              ].map(({method,desc})=>(
                <div key={method} style={{display:"flex",gap:12,marginBottom:8,alignItems:"flex-start"}}>
                  <div style={{background:"#1e293b",borderRadius:4,padding:"2px 8px",fontSize:9,color:"#94a3b8",fontFamily:"monospace",flexShrink:0,whiteSpace:"nowrap"}}>{method}</div>
                  <div style={{color:"#64748b",fontSize:11}}>{desc}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── PROP BOARD TAB ── */}
        {tab==="board" && (
          <div>
            {/* Filters */}
            <div style={{display:"flex",gap:10,marginBottom:16,flexWrap:"wrap",alignItems:"center"}}>
              <input
                placeholder="🔍 Search player..."
                value={search}
                onChange={e=>setSearch(e.target.value)}
                style={{background:"#0f172a",color:"#f8fafc",border:"1px solid #334155",borderRadius:6,padding:"6px 12px",fontSize:12,outline:"none",minWidth:160}}
              />
              {[
                {label:"Team", value:filterTeam, setter:setFilterTeam, options:["ALL",...TEAMS]},
                {label:"Prop", value:filterProp, setter:setFilterProp, options:["ALL",...PROPS]},
                {label:"Type", value:filterType, setter:setFilterType, options:["ALL","Standard","Goblin","Demon"]},
              ].map(({label,value,setter,options})=>(
                <select key={label} value={value} onChange={e=>setter(e.target.value)} style={{
                  background:"#0f172a",color:"#94a3b8",border:"1px solid #334155",borderRadius:6,
                  padding:"6px 10px",fontSize:12,outline:"none",cursor:"pointer",
                }}>
                  {options.map(o=><option key={o} value={o}>{label}: {o}</option>)}
                </select>
              ))}
              <div style={{color:"#334155",fontSize:11,marginLeft:"auto"}}>{filtered.length} rows</div>
            </div>

            {/* Table */}
            <div style={{overflowX:"auto",borderRadius:10,border:"1px solid #1e293b"}}>
              <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
                <thead>
                  <tr style={{background:"#070d1a"}}>
                    {[
                      {col:"player",label:"PLAYER"},
                      {col:"pos",label:"POS"},
                      {col:"team",label:"TEAM"},
                      {col:"opp_team",label:"OPP"},
                      {col:"prop_type",label:"PROP"},
                      {col:"line",label:"LINE"},
                      {col:"pick_type",label:"TYPE"},
                      {col:"nba_player_id",label:"NBA ID"},
                    ].map(({col,label})=>(
                      <th key={col} onClick={()=>toggleSort(col)} style={{
                        padding:"10px 12px",textAlign:"left",color: sortCol===col?"#94a3b8":"#334155",
                        borderBottom:"1px solid #1e293b",fontSize:9,letterSpacing:2,whiteSpace:"nowrap",
                      }}>
                        {label} {sortCol===col ? (sortDir===1?"↑":"↓") : ""}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filtered.slice(0,100).map((row,i)=>(
                    <tr key={i} style={{borderBottom:"1px solid #0f172a"}}>
                      <td style={{padding:"9px 12px",color:"#f8fafc",fontWeight:"bold",whiteSpace:"nowrap"}}>{row.player}</td>
                      <td style={{padding:"9px 12px",color:"#64748b"}}>{row.pos}</td>
                      <td style={{padding:"9px 12px",color:"#94a3b8",fontWeight:"bold"}}>{row.team}</td>
                      <td style={{padding:"9px 12px",color:"#64748b"}}>{row.opp_team||"—"}</td>
                      <td style={{padding:"9px 12px",color:"#94a3b8",whiteSpace:"nowrap"}}>{row.prop_type}</td>
                      <td style={{padding:"9px 12px",color:"#f8fafc",fontWeight:"bold",textAlign:"center"}}>{row.line}</td>
                      <td style={{padding:"9px 12px"}}>
                        <div style={{
                          background:pickBg(row.pick_type),border:`1px solid ${pickColor(row.pick_type)}44`,
                          borderRadius:12,padding:"2px 10px",fontSize:10,color:pickColor(row.pick_type),
                          fontWeight:"bold",display:"inline-block",whiteSpace:"nowrap",
                        }}>
                          {pickEmoji(row.pick_type)} {row.pick_type}
                        </div>
                      </td>
                      <td style={{padding:"9px 12px",color:"#334155",fontSize:10,fontFamily:"monospace"}}>{row.nba_player_id}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {filtered.length > 100 && (
                <div style={{padding:"12px",textAlign:"center",color:"#334155",fontSize:11,borderTop:"1px solid #1e293b"}}>
                  Showing 100 of {filtered.length} rows — use filters to narrow
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
