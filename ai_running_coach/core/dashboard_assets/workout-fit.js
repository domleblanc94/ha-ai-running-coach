/* Small original FIT workout encoder. No network, external library, or coach inference.
 * Format references: https://developer.garmin.com/fit/articles/file-types/workout.html
 * Profile fields/scales checked against official @garmin/fitsdk 21.221.0.
 * Only explicit flat running steps are supported; repetitions must be flattened.
 */
(function (root) {
    'use strict';
    const MAX_STEPS = 50, MAX_NAME_BYTES = 63, MAX_UINT32 = 0xfffffffe;
    const INTENSITY = Object.freeze({active:0, rest:1, warmup:2, cooldown:3, recovery:4});
    const encoder = new TextEncoder();
    function object(value, required, optional=[]) {
        if (!value || typeof value !== 'object' || Array.isArray(value) ||
            required.some(key => !Object.prototype.hasOwnProperty.call(value,key)) ||
            Object.keys(value).some(key => !required.includes(key) && !optional.includes(key)))
            throw new Error('Workout object has missing or unsupported fields');
    }
    function name(value, required=false) {
        if (typeof value !== 'string') throw new Error('Workout names must be text');
        value = value.trim();
        if ((required && !value) || /[\u0000-\u001f\u007f]/.test(value) ||
            Array.from(value).some(c => c.codePointAt(0)>=0xd800 && c.codePointAt(0)<=0xdfff))
            throw new Error('Workout name is missing or contains invalid text');
        if (encoder.encode(value).length>MAX_NAME_BYTES) throw new Error('Workout names must fit within 63 UTF-8 bytes');
        return value;
    }
    function notes(value) {
        if (typeof value!=='string') throw new Error('Workout step notes must be text');
        value=value.trim();
        if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value) ||
            Array.from(value).some(c => c.codePointAt(0)>=0xd800 && c.codePointAt(0)<=0xdfff))
            throw new Error('Workout step notes contain invalid text');
        if (encoder.encode(value).length>200) throw new Error('Workout step notes must fit within 200 UTF-8 bytes');
        return value;
    }
    function scaled(value, scale, label) {
        if (typeof value!=='number' || !Number.isFinite(value) || value<=0 ||
            Math.round(value*scale)<1 || Math.round(value*scale)>MAX_UINT32)
            throw new Error(label+' must be an explicit positive number within the FIT range');
        return value;
    }
    function validate(workout) {
        object(workout,['name','sport','steps']);
        const clean={name:name(workout.name,true),sport:'running',steps:[]};
        if (workout.sport!=='running') throw new Error('Only running FIT workouts are supported');
        if (!Array.isArray(workout.steps) || workout.steps.length<1 || workout.steps.length>MAX_STEPS)
            throw new Error('Workout must contain 1-50 explicit flat steps');
        workout.steps.forEach((step,index) => {
            try {
                object(step,['intensity','duration','target'],['name','notes']);
                if (!Object.prototype.hasOwnProperty.call(INTENSITY,step.intensity)) throw new Error('Unknown step intensity');
                const duration=step.duration, target=step.target;
                object(duration,['type'],['seconds','metres']);
                let d,t;
                if (duration.type==='time') { object(duration,['type','seconds']); d={type:'time',seconds:scaled(duration.seconds,1000,'Duration seconds')}; }
                else if (duration.type==='distance') { object(duration,['type','metres']); d={type:'distance',metres:scaled(duration.metres,100,'Duration metres')}; }
                else if (duration.type==='open') { object(duration,['type']); d={type:'open'}; }
                else throw new Error('Unsupported duration; use time, distance or open');
                object(target,['type'],['lowBpm','highBpm','lowMps','highMps']);
                if (target.type==='open') { object(target,['type']); t={type:'open'}; }
                else if (target.type==='heart_rate') {
                    object(target,['type','lowBpm','highBpm']);
                    if (!Number.isInteger(target.lowBpm) || !Number.isInteger(target.highBpm) ||
                        target.lowBpm<1 || target.highBpm>255 || target.lowBpm>target.highBpm)
                        throw new Error('Heart-rate bounds must be ascending integer bpm, 1-255');
                    t={type:'heart_rate',lowBpm:target.lowBpm,highBpm:target.highBpm};
                } else if (target.type==='speed') {
                    object(target,['type','lowMps','highMps']);
                    const low=scaled(target.lowMps,1000,'Lower speed m/s'),high=scaled(target.highMps,1000,'Upper speed m/s');
                    if (low>high) throw new Error('Speed bounds must be in ascending m/s order');
                    t={type:'speed',lowMps:low,highMps:high};
                } else throw new Error('Unsupported target; use open, heart_rate or speed');
                const result={intensity:step.intensity,duration:d,target:t};
                if (Object.prototype.hasOwnProperty.call(step,'name')) result.name=name(step.name);
                if (Object.prototype.hasOwnProperty.call(step,'notes')) result.notes=notes(step.notes);
                clean.steps.push(result);
            } catch (error) { throw new Error('Workout step '+(index+1)+': '+error.message); }
        });
        return clean;
    }
    function crc(bytes) {
        let result=0;
        for (const byte of bytes) {
            result ^= byte;
            for (let bit=0;bit<8;bit++) result=(result&1) ? (result>>>1)^0xa001 : result>>>1;
        }
        return result & 0xffff;
    }
    function u16(value) { return [value&255,(value>>>8)&255]; }
    function u32(value) { return [value&255,(value>>>8)&255,(value>>>16)&255,(value>>>24)&255]; }
    function text(value,size) {
        const result=new Uint8Array(size); result.set(encoder.encode(value)); return Array.from(result);
    }
    function definition(local,global,fields) {
        return [0x40|local,0,0,...u16(global),fields.length,...fields.flat()];
    }
    function serial(workout) {
        let result=2166136261;
        for (const byte of encoder.encode(JSON.stringify(workout))) result=Math.imul(result^byte,16777619)>>>0;
        return result || 1;
    }
    function encode(workout,options={}) {
        const clean=validate(workout);
        object(options,[],['createdAt']);
        const created=options.createdAt===undefined ? new Date() : new Date(options.createdAt);
        const fitTime=Math.floor((created.getTime()-Date.UTC(1989,11,31))/1000);
        if (!Number.isFinite(fitTime) || fitTime<0 || fitTime>MAX_UINT32) throw new Error('Creation time is outside the FIT date range');
        const data=[];
        // File ID: type=workout, manufacturer=development (not Garmin), own product.
        data.push(...definition(0,0,[[0,1,0x00],[1,2,0x84],[2,2,0x84],[3,4,0x8c],[4,4,0x86]]));
        data.push(0,5,...u16(255),...u16(1),...u32(serial(clean)),...u32(fitTime));
        data.push(...definition(1,26,[[4,1,0x00],[6,2,0x84],[8,64,0x07]]));
        data.push(1,1,...u16(clean.steps.length),...text(clean.name,64));
        data.push(...definition(2,27,[[254,2,0x84],[0,64,0x07],[1,1,0x00],[2,4,0x86],
            [3,1,0x00],[4,4,0x86],[5,4,0x86],[6,4,0x86],[7,1,0x00],[8,201,0x07]]));
        clean.steps.forEach((step,index) => {
            const d=step.duration,t=step.target;
            const durationType=d.type==='time'?0:d.type==='distance'?1:5;
            const durationValue=d.type==='time'?Math.round(d.seconds*1000):d.type==='distance'?Math.round(d.metres*100):0;
            const targetType=t.type==='heart_rate'?1:t.type==='speed'?0:2;
            // Custom-zone selector is zero; absolute HR requires +100 bpm.
            const low=t.type==='heart_rate'?t.lowBpm+100:t.type==='speed'?Math.round(t.lowMps*1000):0;
            const high=t.type==='heart_rate'?t.highBpm+100:t.type==='speed'?Math.round(t.highMps*1000):0;
            data.push(2,...u16(index),...text(step.name||'',64),durationType,...u32(durationValue),
                targetType,...u32(0),...u32(low),...u32(high),INTENSITY[step.intensity],...text(step.notes||'',201));
        });
        // FIT 2.0, profile 21.221; header and file each have CRC-16 checksums.
        const header=[14,0x20,...u16(21221),...u32(data.length),46,70,73,84];
        header.push(...u16(crc(header)));
        const body=new Uint8Array([...header,...data]);
        const result=new Uint8Array(body.length+2);result.set(body);result.set(u16(crc(body)),body.length);
        return result;
    }
    root.WorkoutFit=Object.freeze({encode,validate,MAX_STEPS});
})(typeof globalThis!=='undefined'?globalThis:window);
