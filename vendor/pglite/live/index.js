import{a as O,b as C}from"../chunk-O56QGQDD.js";import"../chunk-3OMHRXTA.js";import{a as I}from"../chunk-NNS5RQRF.js";import{j as P}from"../chunk-QY3QWFKW.js";P();var M=5,U=async(d,y)=>{let p=new Set,$={async query(e,g,a){let m,c,_;if(typeof e!="string"&&(m=e.signal,g=e.params,a=e.callback,c=e.offset,_=e.limit,e=e.query),c===void 0!=(_===void 0))throw new Error("offset and limit must be provided together");let t=c!==void 0&&_!==void 0,T;if(t&&(typeof c!="number"||isNaN(c)||typeof _!="number"||isNaN(_)))throw new Error("offset and limit must be numbers");let E=a?[a]:[],o=I.uuid().replace(/-/g,""),A=!1,v,h,S=async()=>{await d.transaction(async i=>{let s=g&&g.length>0?await O(d,e,g,i):e;await i.exec(`CREATE OR REPLACE TEMP VIEW live_query_${o}_view AS ${s}`);let u=await q(i,`live_query_${o}_view`);await F(i,u,p),t?(await i.exec(`
              PREPARE live_query_${o}_get(int, int) AS
              SELECT * FROM live_query_${o}_view
              LIMIT $1 OFFSET $2;
            `),await i.exec(`
              PREPARE live_query_${o}_get_total_count AS
              SELECT COUNT(*) FROM live_query_${o}_view;
            `),T=(await i.query(`EXECUTE live_query_${o}_get_total_count;`)).rows[0].count,v={...await i.query(`EXECUTE live_query_${o}_get(${_}, ${c});`),offset:c,limit:_,totalCount:T}):(await i.exec(`
              PREPARE live_query_${o}_get AS
              SELECT * FROM live_query_${o}_view;
            `),v=await i.query(`EXECUTE live_query_${o}_get;`)),h=await Promise.all(u.map(f=>i.listen(`"table_change__${f.schema_oid}__${f.table_oid}"`,async()=>{R()})))})};await S();let R=C(async({offset:i,limit:s}={})=>{if(!t&&(i!==void 0||s!==void 0))throw new Error("offset and limit cannot be provided for non-windowed queries");if(i&&(typeof i!="number"||isNaN(i))||s&&(typeof s!="number"||isNaN(s)))throw new Error("offset and limit must be numbers");c=i??c,_=s??_;let u=async(f=0)=>{if(E.length!==0){try{t?v={...await d.query(`EXECUTE live_query_${o}_get(${_}, ${c});`),offset:c,limit:_,totalCount:T}:v=await d.query(`EXECUTE live_query_${o}_get;`)}catch(l){let n=l.message;if(n.startsWith(`prepared statement "live_query_${o}`)&&n.endsWith("does not exist")){if(f>M)throw l;await S(),u(f+1)}else throw l}if(N(E,v),t){let l=(await d.query(`EXECUTE live_query_${o}_get_total_count;`)).rows[0].count;l!==T&&(T=l,R())}}};await u()}),w=i=>{if(A)throw new Error("Live query is no longer active and cannot be subscribed to");E.push(i)},r=async i=>{i?E=E.filter(s=>s!==i):E=[],E.length===0&&!A&&(A=!0,await d.transaction(async s=>{await Promise.all(h.map(u=>u(s))),await s.exec(`
              DROP VIEW IF EXISTS live_query_${o}_view;
              DEALLOCATE live_query_${o}_get;
              ${t?`DEALLOCATE live_query_${o}_get_total_count;`:""}
            `)}))};return m?.aborted?await r():m?.addEventListener("abort",()=>{r()},{once:!0}),N(E,v),{initialResults:v,subscribe:w,unsubscribe:r,refresh:R}},async changes(e,g,a,m){let c;if(typeof e!="string"&&(c=e.signal,g=e.params,a=e.key,m=e.callback,e=e.query),!a)throw new Error("key is required for changes queries");let _=m?[m]:[],t=I.uuid().replace(/-/g,""),T=!1,E=1,o,A,v=async()=>{await d.transaction(async r=>{let i=await O(d,e,g,r);await r.query(`CREATE OR REPLACE TEMP VIEW live_query_${t}_view AS ${i}`);let s=await q(r,`live_query_${t}_view`);await F(r,s,p);let u=[...(await r.query(`
                SELECT column_name, data_type, udt_name
                FROM information_schema.columns 
                WHERE table_name = 'live_query_${t}_view'
              `)).rows,{column_name:"__after__",data_type:"integer"}];await r.exec(`
            CREATE TEMP TABLE live_query_${t}_state1 (LIKE live_query_${t}_view INCLUDING ALL);
            CREATE TEMP TABLE live_query_${t}_state2 (LIKE live_query_${t}_view INCLUDING ALL);
          `);for(let f of[1,2]){let l=f===1?2:1;await r.exec(`
              PREPARE live_query_${t}_diff${f} AS
              WITH
                prev AS (SELECT LAG("${a}") OVER () as __after__, * FROM live_query_${t}_state${l}),
                curr AS (SELECT LAG("${a}") OVER () as __after__, * FROM live_query_${t}_state${f}),
                data_diff AS (
                  -- INSERT operations: Include all columns
                  SELECT 
                    'INSERT' AS __op__,
                    ${u.map(({column_name:n})=>`curr."${n}" AS "${n}"`).join(`,
`)},
                    ARRAY[]::text[] AS __changed_columns__
                  FROM curr
                  LEFT JOIN prev ON curr.${a} = prev.${a}
                  WHERE prev.${a} IS NULL
                UNION ALL
                  -- DELETE operations: Include only the primary key
                  SELECT 
                    'DELETE' AS __op__,
                    ${u.map(({column_name:n,data_type:L,udt_name:b})=>n===a?`prev."${n}" AS "${n}"`:`NULL${L==="USER-DEFINED"?`::${b}`:""} AS "${n}"`).join(`,
`)},
                      ARRAY[]::text[] AS __changed_columns__
                  FROM prev
                  LEFT JOIN curr ON prev.${a} = curr.${a}
                  WHERE curr.${a} IS NULL
                UNION ALL
                  -- UPDATE operations: Include only changed columns
                  SELECT 
                    'UPDATE' AS __op__,
                    ${u.map(({column_name:n,data_type:L,udt_name:b})=>n===a?`curr."${n}" AS "${n}"`:`CASE 
                              WHEN curr."${n}" IS DISTINCT FROM prev."${n}" 
                              THEN curr."${n}"
                              ELSE NULL${L==="USER-DEFINED"?`::${b}`:""}
                              END AS "${n}"`).join(`,
`)},
                      ARRAY(SELECT unnest FROM unnest(ARRAY[${u.filter(({column_name:n})=>n!==a).map(({column_name:n})=>`CASE
                              WHEN curr."${n}" IS DISTINCT FROM prev."${n}" 
                              THEN '${n}' 
                              ELSE NULL 
                              END`).join(", ")}]) WHERE unnest IS NOT NULL) AS __changed_columns__
                  FROM curr
                  INNER JOIN prev ON curr.${a} = prev.${a}
                  WHERE NOT (curr IS NOT DISTINCT FROM prev)
                )
              SELECT * FROM data_diff;
            `)}A=await Promise.all(s.map(f=>r.listen(`"table_change__${f.schema_oid}__${f.table_oid}"`,async()=>{h()})))})};await v();let h=C(async()=>{if(_.length===0&&o)return;let r=!1;for(let i=0;i<5;i++)try{await d.transaction(async s=>{await s.exec(`
                INSERT INTO live_query_${t}_state${E} 
                  SELECT * FROM live_query_${t}_view;
              `),o=await s.query(`EXECUTE live_query_${t}_diff${E};`),E=E===1?2:1,await s.exec(`
                TRUNCATE live_query_${t}_state${E};
              `)});break}catch(s){if(s.message===`relation "live_query_${t}_state${E}" does not exist`){r=!0,await v();continue}else throw s}D(_,[...r?[{__op__:"RESET"}]:[],...o.rows])}),S=r=>{if(T)throw new Error("Live query is no longer active and cannot be subscribed to");_.push(r)},R=async r=>{r?_=_.filter(i=>i!==r):_=[],_.length===0&&!T&&(T=!0,await d.transaction(async i=>{await Promise.all(A.map(s=>s(i))),await i.exec(`
              DROP VIEW IF EXISTS live_query_${t}_view;
              DROP TABLE IF EXISTS live_query_${t}_state1;
              DROP TABLE IF EXISTS live_query_${t}_state2;
              DEALLOCATE live_query_${t}_diff1;
              DEALLOCATE live_query_${t}_diff2;
            `)}))};return c?.aborted?await R():c?.addEventListener("abort",()=>{R()},{once:!0}),await h(),{fields:o.fields.filter(r=>!["__after__","__op__","__changed_columns__"].includes(r.name)),initialChanges:o.rows,subscribe:S,unsubscribe:R,refresh:h}},async incrementalQuery(e,g,a,m){let c;if(typeof e!="string"&&(c=e.signal,g=e.params,a=e.key,m=e.callback,e=e.query),!a)throw new Error("key is required for incremental queries");let _=m?[m]:[],t=new Map,T=new Map,E=[],o=!0,{fields:A,unsubscribe:v,refresh:h}=await $.changes(e,g,a,w=>{for(let s of w){let{__op__:u,__changed_columns__:f,...l}=s;switch(u){case"RESET":t.clear(),T.clear();break;case"INSERT":t.set(l[a],l),T.set(l.__after__,l[a]);break;case"DELETE":{let n=t.get(l[a]);t.delete(l[a]),n.__after__!==null&&T.delete(n.__after__);break}case"UPDATE":{let n={...t.get(l[a])??{}};for(let L of f)n[L]=l[L],L==="__after__"&&T.set(l.__after__,l[a]);t.set(l[a],n);break}}}let r=[],i=null;for(let s=0;s<t.size;s++){let u=T.get(i),f=t.get(u);if(!f)break;let l={...f};delete l.__after__,r.push(l),i=u}E=r,o||N(_,{rows:r,fields:A})});o=!1,N(_,{rows:E,fields:A});let S=w=>{_.push(w)},R=async w=>{w?_=_.filter(r=>r!==w):_=[],_.length===0&&await v()};return c?.aborted?await R():c?.addEventListener("abort",()=>{R()},{once:!0}),{initialResults:{rows:E,fields:A},subscribe:S,unsubscribe:R,refresh:h}}};return{namespaceObj:$}},Q={name:"Live Queries",setup:U};async function q(d,y){return(await d.query(`
      WITH RECURSIVE view_dependencies AS (
        -- Base case: Get the initial view's dependencies
        SELECT DISTINCT
          cl.relname AS dependent_name,
          n.nspname AS schema_name,
          cl.oid AS dependent_oid,
          n.oid AS schema_oid,
          cl.relkind = 'v' AS is_view
        FROM pg_rewrite r
        JOIN pg_depend d ON r.oid = d.objid
        JOIN pg_class cl ON d.refobjid = cl.oid
        JOIN pg_namespace n ON cl.relnamespace = n.oid
        WHERE
          r.ev_class = (
              SELECT oid FROM pg_class WHERE relname = $1 AND relkind = 'v'
          )
          AND d.deptype = 'n'

        UNION ALL

        -- Recursive case: Traverse dependencies for views
        SELECT DISTINCT
          cl.relname AS dependent_name,
          n.nspname AS schema_name,
          cl.oid AS dependent_oid,
          n.oid AS schema_oid,
          cl.relkind = 'v' AS is_view
        FROM view_dependencies vd
        JOIN pg_rewrite r ON vd.dependent_name = (
          SELECT relname FROM pg_class WHERE oid = r.ev_class AND relkind = 'v'
        )
        JOIN pg_depend d ON r.oid = d.objid
        JOIN pg_class cl ON d.refobjid = cl.oid
        JOIN pg_namespace n ON cl.relnamespace = n.oid
        WHERE d.deptype = 'n'
      )
      SELECT DISTINCT
        dependent_name AS table_name,
        schema_name,
        dependent_oid AS table_oid,
        schema_oid
      FROM view_dependencies
      WHERE NOT is_view; -- Exclude intermediate views
    `,[y])).rows.map($=>({table_name:$.table_name,schema_name:$.schema_name,table_oid:$.table_oid,schema_oid:$.schema_oid}))}async function F(d,y,p){let $=y.filter(e=>!p.has(`${e.schema_oid}_${e.table_oid}`)).map(e=>`
      CREATE OR REPLACE FUNCTION "_notify_${e.schema_oid}_${e.table_oid}"() RETURNS TRIGGER AS $$
      BEGIN
        PERFORM pg_notify('table_change__${e.schema_oid}__${e.table_oid}', '');
        RETURN NULL;
      END;
      $$ LANGUAGE plpgsql;
      CREATE OR REPLACE TRIGGER "_notify_trigger_${e.schema_oid}_${e.table_oid}"
      AFTER INSERT OR UPDATE OR DELETE ON "${e.schema_name}"."${e.table_name}"
      FOR EACH STATEMENT EXECUTE FUNCTION "_notify_${e.schema_oid}_${e.table_oid}"();
      `).join(`
`);$.trim()!==""&&await d.exec($),y.map(e=>p.add(`${e.schema_oid}_${e.table_oid}`))}var N=(d,y)=>{for(let p of d)p(y)},D=(d,y)=>{for(let p of d)p(y)};export{Q as live};
//# sourceMappingURL=index.js.map