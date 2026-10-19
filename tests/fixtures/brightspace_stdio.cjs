// Synthetic MCP peer only: no imports of Brightspace, authentication or network.
const readline = require('node:readline');
const readNames = ['get_my_courses','get_course_content','get_syllabus','get_announcements','get_assignments','get_upcoming_due_dates','get_calendar_events','search_course','download_file'];
const denied = ['sign_out','submit_assignment','mark_read',...Array.from({length:14},(_,i)=>`synthetic_write_${i}`)];
const tools = [...readNames,...denied].map(name=>({name,
  inputSchema: {type:'object',properties:name==='get_my_courses'?{activeOnly:{type:'boolean'}}:{courseId:{type:'integer'},...(name==='download_file'?{topicId:{type:'integer'},downloadPath:{type:'string'}}:{})},required:name==='get_my_courses'?[]:['courseId']},
  annotations:{readOnlyHint:!['get_syllabus','download_file',...denied].includes(name)}}));
const text = value => ({type:'text',text:JSON.stringify(value)});
readline.createInterface({input:process.stdin}).on('line',line=>{
  const req = JSON.parse(line); if(req.id===undefined)return;
  let result;
  if(req.method==='initialize') {
    if(process.env.TELEGRAM_BOT_TOKEN || process.env.WXO_API_KEY || process.env.NODE_OPTIONS || process.env.D2L_ACCESS_TOKEN)process.exit(9);
    if(process.env.D2L_BASE_URL!=='https://school.example' || process.env.D2L_NO_UPDATE_CHECK!=='1')process.exit(10);
    result={protocolVersion:req.params.protocolVersion,capabilities:{tools:{}},serverInfo:{name:'synthetic-brightspace',version:'3.13.18'}};
  } else if(req.method==='tools/list') result={tools};
  else if(req.method==='tools/call') {
    const {name,arguments:a}=req.params;
    if(!readNames.includes(name) || 'downloadPath' in a || 'fileId' in a)process.exit(11);
    if(name==='get_my_courses')result={content:[text([{id:123,name:'Synthetic ML'}])]};
    else if(name==='get_course_content')result={content:[text({contentTree:[{id:5,topicId:5,title:'Synthetic plan.pdf',topicType:'file'}]})]};
    else if(name==='download_file')result={content:[text({mode:'inline',mimeType:'application/pdf',representation:'extracted_text',truncated:false}),{type:'text',text:'Synthetic plan: exam date TBC.'}]};
    else result={content:[text({description:'Synthetic overview'})]};
  } else if(req.method==='ping')result={};
  else { process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:req.id,error:{code:-32601,message:'unknown synthetic method'}})+'\n');return; }
  process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:req.id,result})+'\n');
});
