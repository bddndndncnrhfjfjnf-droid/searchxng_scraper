/*
@licstart  The following is the entire license notice for the
JavaScript code in this page.

Copyright (c) 2025 Xe Iaso <xe.iaso@techaro.lol>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

Includes code from https://github.com/aws/aws-sdk-js-crypto-helpers which is
used under the terms of the Apache 2 license.

@licend  The above is the entire license notice
for the JavaScript code in this page.
*/
(()=>{var h=new TextEncoder,y=async e=>{let r=h.encode(e);return await crypto.subtle.digest("SHA-256",r)},g=e=>e.reduce((r,s)=>r+s.toString(16).padStart(2,"0"),"");addEventListener("message",async({data:e})=>{let{data:r,difficulty:s,threads:f}=e,t=e.nonce,d=t===0,i=0,c=Math.floor(s/2),l=s%2!==0;for(;;){let u=await y(r+t),o=new Uint8Array(u),n=!0;for(let a=0;a<c;a++)if(o[a]!==0){n=!1;break}if(n&&l&&o[c]>>4!==0&&(n=!1),n){let a=g(o);postMessage({hash:a,data:r,difficulty:s,nonce:t});return}t+=f,i++,t%1!==0&&(t=Math.trunc(t)),d&&(i&1023)===0&&postMessage(t)}});})();
//# sourceMappingURL=sha256-webcrypto.mjs.map

