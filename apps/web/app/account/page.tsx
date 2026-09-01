"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { AppShell } from "../../components/app-shell";
import { clearCachedPlan } from "../../lib/plan-cache";
import { clearSavedSession } from "../../lib/session";
import { supabase } from "../../lib/supabase";

const genericEmailMessage="If that address can receive this email, we sent the next step. Check spam as well.";
const redirectUrl=()=>typeof window === "undefined" ? undefined : `${window.location.origin}/account`;

export default function Account(){
  const [email,setEmail]=useState(""); const [password,setPassword]=useState(""); const [newPassword,setNewPassword]=useState("");
  const [signedIn,setSignedIn]=useState<string|undefined>(); const [message,setMessage]=useState(""); const [recovering,setRecovering]=useState(false);
  useEffect(()=>{
    supabase?.auth.getUser().then(({data})=>setSignedIn(data.user?.email));
    const {data}=supabase?.auth.onAuthStateChange((event,session)=>{setSignedIn(session?.user.email);if(event==="PASSWORD_RECOVERY")setRecovering(true);})??{data:{subscription:{unsubscribe(){}}}};
    return()=>data.subscription.unsubscribe();
  },[]);
  const submit=async(event:FormEvent,signup:boolean)=>{
    event.preventDefault(); if(!supabase){setMessage("Account setup is not configured yet.");return;}
    const result=signup
      ?await supabase.auth.signUp({email,password,options:{emailRedirectTo:redirectUrl()}})
      :await supabase.auth.signInWithPassword({email,password});
    setMessage(result.error?.message??(signup?genericEmailMessage:"Signed in."));
  };
  const resend=async()=>{if(!supabase||!email){setMessage("Enter your email address first.");return;}await supabase.auth.resend({type:"signup",email,options:{emailRedirectTo:redirectUrl()}});setMessage(genericEmailMessage);};
  const reset=async()=>{if(!supabase||!email){setMessage("Enter your email address first.");return;}await supabase.auth.resetPasswordForEmail(email,{redirectTo:redirectUrl()});setMessage(genericEmailMessage);};
  const saveNewPassword=async(event:FormEvent)=>{event.preventDefault();if(!supabase){return;}const result=await supabase.auth.updateUser({password:newPassword});setMessage(result.error?.message??"Password updated. You can now sign in.");if(!result.error)setRecovering(false);};
  const signOut=async()=>{await supabase?.auth.signOut();clearSavedSession();clearCachedPlan();await caches?.keys?.().then(keys=>Promise.all(keys.filter(key=>key.startsWith("rowing-plan-")).map(key=>caches.delete(key))));setMessage("Signed out and removed this device’s saved plan data.");};
  if(recovering)return <AppShell title="Set a new password"><form className="onboarding" onSubmit={saveNewPassword}><h2>Set a new password</h2><label>New password<input type="password" value={newPassword} onChange={event=>setNewPassword(event.target.value)} minLength={8} required/></label><button>Save new password</button><p className="status">{message}</p></form></AppShell>;
  if(signedIn)return <AppShell title="Account"><section className="empty"><h2>Signed in</h2><p>{signedIn}</p><p>Account identity is separate from your Athlete Profile.</p><Link href="/profile">Open Athlete Profile</Link><button className="quiet" onClick={signOut}>Sign out</button><p className="status">{message}</p></section></AppShell>;
  return <AppShell title="Account"><form className="onboarding" onSubmit={event=>submit(event,false)}><h2>Private account</h2><p>Your plans and optional health tracking are private to your account.</p><label>Email<input type="email" value={email} onChange={event=>setEmail(event.target.value)} required/></label><label>Password<input type="password" value={password} onChange={event=>setPassword(event.target.value)} minLength={8} required/></label><button>Sign in</button><button type="button" className="quiet" onClick={event=>submit(event as unknown as FormEvent,true)}>Create account</button><div className="account-help"><button type="button" className="text-button" onClick={resend}>Resend confirmation email</button><button type="button" className="text-button" onClick={reset}>Reset password</button></div><p className="status">{message}</p></form></AppShell>;
}
