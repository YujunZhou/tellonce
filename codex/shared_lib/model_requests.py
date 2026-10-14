"""Label model work at its call site and give each request one retry owner."""
import re
import time


class ModelRequestError(RuntimeError):
    retry_exhausted=True
    def __init__(self, role, attempts, error_types, *, retry_after_s=None):
        self.attempts=attempts
        self.error_types=tuple(error_types)
        self.retry_after_s=retry_after_s
        super().__init__(f'{role} request unavailable after {attempts} attempts')


def exhausted_request(error):
    """Do not give a queue another retry budget after a bounded model request."""
    seen=set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error,ModelRequestError):
            return True
        error=error.__cause__ or error.__context__
    return False


def learning_recovery_delay(error):
    """One separate recovery epoch for transient transport failures and for answers that failed validation.

    A request whose answers were produced but rejected by the local validator (2026-10-04: every failed learner
    turn in rounds 20-23 was of this kind, e.g. an evidence span that is not a literal quote) gets one fresh
    request under a new key, exactly like a transport timeout; a second exhaustion is final."""
    transient={'provider_timeout','provider_rate_limited','provider_service_unavailable',
               'provider_network_unavailable','response_validation_failed'}
    seen=set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error,ModelRequestError):
            if not error.error_types or any(code not in transient for code in error.error_types):
                return None
            delay=error.retry_after_s
            if delay is None:
                return 5.0
            if type(delay) in {int,float} and 0<=delay<=60:
                return max(5.0,float(delay))
            return None
        error=error.__cause__ or error.__context__
    return None


def bind_context(invoke, context):
    """Create an immutable role-call scope from host identities, never prompts."""
    fields={'namespace','phase','scope','task_id','session_id','event_id'}
    if (not isinstance(context,dict) or set(context)!=fields or
            any(not isinstance(v,str) or not v.strip() or not 1<=len(v)<=480 for v in context.values()) or
            context['phase'] not in {'preparation','training','test'} or
            context['scope'] not in {'user_event','publication'}):
        raise ValueError('complete bounded model invocation origin required')
    method=(getattr(invoke,'for_context',None) if callable(getattr(type(invoke),'for_context',None)) else None)
    if method is None:
        return invoke  # Plain single-attempt synthetic/compatibility callbacks.
    bound=method(dict(context))
    if not callable(bound):
        raise ValueError('a model context must produce an explicit callable owner')
    return bound


def request_validated(invoke, prompt, *, role, validate, max_attempts=2, request_key=None, repair=None):
    if (not callable(invoke) or not callable(validate) or not isinstance(prompt,str) or
            not isinstance(role,str) or not re.fullmatch('[a-z][a-z0-9_]{0,63}',role) or
            type(max_attempts) is not int or max_attempts not in {1,2} or
            (repair is not None and not callable(repair)) or
            (request_key is not None and (not isinstance(request_key,str) or not request_key.strip() or len(request_key)>480))):
        raise ValueError('explicit role, bounded attempts and response validator required')
    owner=(getattr(invoke,'request_validated',None)
           if callable(getattr(type(invoke),'request_validated',None)) else None)
    if callable(owner):
        # The experiment owner supplies task/phase identity, durable request
        # recovery and a single-attempt provider. Never wrap it in another loop.
        keyed={'request_key':request_key} if request_key is not None else {}
        repair_owner=(getattr(invoke,'request_validated_with_repair',None)
                      if callable(getattr(type(invoke),'request_validated_with_repair',None)) else None)
        if repair is not None and repair_owner is not None:
            result=repair_owner(prompt,role=role,validate=validate,max_attempts=max_attempts,
                                repair=repair,**keyed)
        else:
            # Legacy external owners keep their original retry policy; only an
            # explicit protocol implementation may own a changed second input.
            result=owner(prompt,role=role,validate=validate,max_attempts=max_attempts,**keyed)
        if (not isinstance(result,tuple) or len(result)!=2 or type(result[1]) is not int or
                not 0<=result[1]<=max_attempts):
            raise ValueError('managed model request must return its validated value and actual bounded attempt count')
        return result
    errors=[]
    next_prompt=prompt
    for attempt in range(1,max_attempts+1):
        raw=None
        try:
            raw=invoke(next_prompt)
            return validate(raw),attempt
        except Exception as exc:
            if exhausted_request(exc):
                raise
            errors.append(type(exc).__name__)
            delay=getattr(exc,'retry_after_s',None)
            if getattr(exc,'retryable',True) is False or attempt==max_attempts:
                break
            if repair is not None and isinstance(raw,str):
                next_prompt=repair(prompt,raw,exc)
                if not isinstance(next_prompt,str) or not next_prompt.strip():
                    raise ValueError('repair must return a nonempty bounded prompt')
            if delay is not None:
                if type(delay) not in {int,float} or not 0<=delay<=60:
                    break
                time.sleep(delay)
    raise ModelRequestError(role,attempt,errors,retry_after_s=delay) from None
