import assert from "node:assert/strict";
import test from "node:test";
import {isForbiddenAddress} from "./url-policy.js";
test("blocks private and metadata addresses",()=>{for(const ip of ["127.0.0.1","10.1.2.3","169.254.169.254","192.168.1.1","::1","fd00::1"])assert.equal(isForbiddenAddress(ip),true)});
test("allows public addresses",()=>assert.equal(isForbiddenAddress("8.8.8.8"),false));
