from dataclasses import asdict
import time
from .preprocessing import preprocess, LayoutError
from .validator import validate, is_confidence

FIELD_ORDER=('nsx_anchor','date','dm_anchor','dm','ab','time')


def candidate_raw(readings):
    t={name:readings[name]['text'] for name in FIELD_ORDER}
    # The separator layout is configured; NSX/DM and all values are actually read.
    return f"{t['nsx_anchor']} {t['date']} {t['dm_anchor']} {t['dm']}\n{t['ab']} {t['time']}"


def choose_candidate(candidates,minimum):
    """Selection never uses expected values, parser validity, or PASS status."""
    def score(candidate):
        values=[candidate['fields'][f]['confidence'] for f in FIELD_ORDER]
        return min(values) if all(is_confidence(v) for v in values) else -1.0
    selected=max(candidates,key=score)
    eligible=[c for c in candidates if score(c)>=minimum]
    signatures={tuple(c['fields'][f]['text'] for f in FIELD_ORDER) for c in eligible}
    return selected,len(signatures)>1,score(selected)


def inspect_image(image,roi,engine,expected,deadline_ms=100):
    started=time.perf_counter()
    report={'candidates':[], 'ocr_device':str(getattr(engine,'device','unknown')),
        'expected':dict(ab=expected.ab,dm=expected.dm,
        minimum_confidence=expected.minimum_confidence,
        check_nsx_date=expected.check_nsx_date,
        expected_date=expected.expected_date.isoformat() if expected.expected_date else None,
        maximum_angle_deviation=expected.maximum_angle_deviation),
        'ocr_mode':'FAST_100MS','deadline_ms':deadline_ms,'stage_ms':{}}
    artifacts={}
    try:
        if roi is None:
            raise LayoutError('No fixed ROI configured')
        try:
            crop=roi.crop(image)
        except ValueError as exc:
            raise LayoutError(str(exc)) from exc
        report['bbox']=roi.bounds(image.shape)
        artifacts['roi']=crop
        stage=time.perf_counter()
        variants,fields,metrics=preprocess(crop)
        report['stage_ms']['preprocess']=round((time.perf_counter()-stage)*1000,2)
        artifacts.update(variants)
        report.update(metrics=metrics,field_boxes=fields)
        stage=time.perf_counter()
        if hasattr(engine,'read_fields_fast'):
            numeric_variant=metrics['numeric_variant']
            padding,gap=(2,12) if numeric_variant=='gray' else (6,18)
            readings={field:asdict(value) for field,value in
                      engine.read_fields_fast(variants['gray'],fields,variants[numeric_variant],
                                              variants['otsu'],variants[metrics['dm_variant']],
                                              padding,gap).items()}
            report['candidates'].append(dict(name=f'{numeric_variant}_numeric_fast_1pass',
                image_variant=numeric_variant,
                decoder='greedy',fields=readings,raw=candidate_raw(readings)))
        else:
            # Compatibility path for test doubles and alternate OCR engines.
            report['ocr_mode']='COMPATIBILITY'
            for image_variant in ('gray','otsu'):
                for decoder in ('greedy','beamsearch'):
                    readings={field:asdict(engine.read_field(variants[image_variant][t:b,l:r],field,decoder))
                              for field,(l,t,r,b) in fields.items()}
                    candidate={'name':f'{image_variant}_{decoder}','image_variant':image_variant,
                               'decoder':decoder,'fields':readings,'raw':candidate_raw(readings)}
                    report['candidates'].append(candidate)
        report['stage_ms']['ocr']=round((time.perf_counter()-stage)*1000,2)
        selected,conflict,score=choose_candidate(report['candidates'],expected.minimum_confidence)
        report['selected']=selected['name']
        artifacts['final']=variants[selected['image_variant']]
        confidence={name:selected['fields'][name]['confidence'] for name in ('date','dm','ab','time')}
        result=validate(selected['raw'],confidence,expected).to_dict()
        if score<expected.minimum_confidence:
            result.update(result='FAIL_OCR_LOW_CONFIDENCE',error_reason='At least one field or anchor has low confidence')
        elif conflict:
            result.update(result='FAIL_OCR',error_reason='Conflicting high-confidence candidates; no guessing')
        report['validation']=result
        report['anchor_confidence']={name:selected['fields'][name]['confidence'] for name in ('nsx_anchor','dm_anchor')}
    except LayoutError as exc:
        report['validation']=validate('',{},expected,roi_found=False).to_dict()
        report['validation']['error_reason']=str(exc)
    except Exception as exc:
        import traceback
        report['traceback']=traceback.format_exc()
        report['validation']=dict(result='FAIL_OCR',raw='',parsed=None,confidence={},
                                  overall_confidence=None,error_reason=f'{type(exc).__name__}: {exc}')
    report['processing_ms']=round((time.perf_counter()-started)*1000,2)
    report['deadline_met']=report['processing_ms']<=deadline_ms
    if not report['deadline_met'] and report['validation']['result']=='PASS':
        report['validation'].update(result='FAIL_DEADLINE',
            error_reason=f'OCR exceeded {deadline_ms} ms deadline')
    return report,artifacts
