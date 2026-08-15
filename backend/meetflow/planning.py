"""Analysis delegation validation shared by the submission loop and parent graph."""
from .domain import Delegation, DomainError


def validate_plan(jobs):
    values = [Delegation.model_validate(job).model_dump() for job in jobs]
    index = {value['id']: value for value in values}
    if not values or len(index) != len(values) or len(values) > 12:
        raise DomainError('委派计划为空、编号重复或任务数超限')
    visiting, done = set(), set()

    def visit(key):
        if key not in index:
            raise DomainError('计划引用不存在的依赖')
        if key in visiting:
            raise DomainError('计划存在依赖环')
        if key in done:
            return
        visiting.add(key)
        for dependency in index[key]['depends_on']:
            visit(dependency)
        visiting.remove(key)
        done.add(key)

    for key in index:
        visit(key)
    auditors = [value for value in values if value['role'] == 'auditor']
    if len(auditors) != 1 or not any(value['role'] == 'planner' for value in values):
        raise DomainError('计划须包含一个审查任务和执行规划任务')
    ancestors = set()

    def collect(key):
        for dependency in index[key]['depends_on']:
            if dependency not in ancestors:
                ancestors.add(dependency)
                collect(dependency)

    collect(auditors[0]['id'])
    if ancestors != set(index) - {auditors[0]['id']}:
        raise DomainError('审查任务未依赖全部前置调查结果')
    return values
