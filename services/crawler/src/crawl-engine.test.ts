import assert from "node:assert/strict";
import test from "node:test";

import {crawlSite, extractSitemapUrls, normalizeCandidate} from "./crawl-engine.js";
import type {FetchResource, FetchedResource} from "./types.js";

function resource(url:string,body:string,contentType:string):FetchedResource{return {requestedUrl:url,finalUrl:url,status:200,contentType,body,rendered:false}}

test("normalization keeps same-host clean URLs only",()=>{const base=new URL("https://example.com/");assert.equal(normalizeCandidate("/about/#team",base,"example.com"),"https://example.com/about");assert.equal(normalizeCandidate("/search?q=x",base,"example.com"),null);assert.equal(normalizeCandidate("https://other.example/x",base,"example.com"),null)});

test("sitemap parser extracts nested locations",()=>{assert.deepEqual(extractSitemapUrls("<urlset><url><loc>https://example.com/a</loc></url><url><loc>https://example.com/b</loc></url></urlset>"),["https://example.com/a","https://example.com/b"])});

test("sitemap parser stops at its URL budget",()=>{assert.deepEqual(extractSitemapUrls("<urlset><url><loc>https://example.com/a</loc></url><url><loc>https://example.com/b</loc></url></urlset>",1),["https://example.com/a"])});

test("crawl honors robots and extracts bounded page evidence",async()=>{
  const responses=new Map<string,FetchedResource>([
    ["https://example.com/robots.txt",resource("https://example.com/robots.txt","User-agent: *\nDisallow: /private\nSitemap: https://example.com/sitemap.xml","text/plain")],
    ["https://example.com/sitemap.xml",resource("https://example.com/sitemap.xml","<urlset><url><loc>https://example.com/about</loc></url><url><loc>https://example.com/private</loc></url></urlset>","application/xml")],
    ["https://example.com/",resource("https://example.com/","<html><head><title>Home</title><meta name='description' content='Welcome'><meta name='robots' content='index, follow'><link rel='canonical' href='/'><script type='application/ld+json'>{\"@type\":\"Organization\"}</script></head><body><h1>Main heading</h1><a href='/about' rel='nofollow'>About</a><a href='/private'>Private</a><a href='/search?q=x'>Trap</a></body></html>","text/html")],
    ["https://example.com/about",resource("https://example.com/about","<html><head><title>About</title></head><body><h1>About us</h1><p>Two words</p></body></html>","text/html")],
  ]);
  const fetcher:FetchResource=async url=>{const found=responses.get(url.toString());if(!found)throw new Error(`unexpected:${url}`);return found};
  const result=await crawlSite("https://example.com",10,fetcher);
  assert.deepEqual(result.observations.map(page=>page.title),["Home","About"]);
  assert.equal(result.observations[0]?.metaDescription,"Welcome");
  assert.equal(result.observations[0]?.canonicalUrl,"https://example.com/");
  assert.deepEqual(result.observations[0]?.robotsDirectives,["index","follow"]);
  assert.deepEqual(result.observations[0]?.structuredData,[{"@type":"Organization"}]);
  assert.deepEqual(result.observations[0]?.links[0],{targetUrl:"https://example.com/about",anchorText:"About",relValues:["nofollow"]});
  assert.equal(result.skippedByRobots,1);
});

test("crawl depth prevents traversal beyond the configured boundary",async()=>{
  const responses=new Map<string,FetchedResource>([
    ["https://depth.example/robots.txt",resource("https://depth.example/robots.txt","","text/plain")],
    ["https://depth.example/",resource("https://depth.example/","<html><body><a href='/level-1'>One</a></body></html>","text/html")],
    ["https://depth.example/level-1",resource("https://depth.example/level-1","<html><body><a href='/level-2'>Two</a></body></html>","text/html")],
    ["https://depth.example/level-2",resource("https://depth.example/level-2","<html><body>Too deep</body></html>","text/html")],
  ]);
  const requested:string[]=[];
  const fetcher:FetchResource=async url=>{requested.push(url.toString());const found=responses.get(url.toString());if(!found)throw new Error("missing");return found};
  const result=await crawlSite("https://depth.example",10,fetcher,1);
  assert.deepEqual(result.observations.map(page=>page.normalizedUrl),["https://depth.example/","https://depth.example/level-1"]);
  assert.ok(!requested.includes("https://depth.example/level-2"));
});
