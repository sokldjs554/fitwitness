import {useState} from 'react';
export function DrawingImage({src}:{src:string}) {
 const [attempt,setAttempt]=useState(0),[failed,setFailed]=useState(false),[loaded,setLoaded]=useState(false);
 return <>
  {!failed&&!loaded&&<span role="status">원본 도면을 불러오는 중…</span>}
  <img src={attempt?`${src}?retry=${attempt}`:src} alt="원본 도면과 근거 영역" style={{display:failed?'none':undefined}} onLoad={()=>setLoaded(true)} onError={()=>setFailed(true)}/>
  {failed&&<div role="alert" className="loading-panel"><p>도면 이미지를 불러오지 못했습니다.</p><button onClick={()=>{setLoaded(false);setFailed(false);setAttempt(a=>a+1);}}>도면 다시 불러오기</button></div>}
 </>;
}
