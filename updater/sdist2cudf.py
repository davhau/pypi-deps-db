#!/usr/bin/env python
import functools, json, os, re, sys


def openAll(filePaths):
    for filePath in filePaths:
        with open(filePath) as openFile:
            yield openFile


def loadAll(jsonFiles):
    for jsonFile in jsonFiles:
        yield json.load(jsonFile)


def pkgAll(pkgsPages):
    for pkgs in pkgsPages:
        for pkgName, pkgVersions in pkgs.items():
            for pkgVersion, pyVersions in pkgVersions.items():
                if type(pyVersions) == str:
                    continue

                for pyVersion, pkgInfo in pyVersions.items():
                    while type(pkgInfo) == str:
                        pkgInfo = pyVersions[pkgInfo]

                    yield {
                        'name':     pkgName,
                        'version':  pkgVersion,
                        'python':   pyVersion,
                        'requires': pkgInfo.get('install_requires', [])
                    }


@functools.cache
def nameAsCudf(name):
    return name.replace('_', '-')\
            .replace('[',    '(')\
            .replace(']',    ')')\


NO_VER = re.compile('[^0-9.]+')


@functools.cache
def verAsCudf(ver):
    ver    = NO_VER.sub('', ver)
    parts  = ver.split('.')
    major  = parts[0] if len(parts[0]) <= 3 else parts[0][-2:]
    minor  = parts[1].ljust(3, '0') if len(parts) > 1 else '000'
    patch  = parts[2].ljust(3, '0') if len(parts) > 2 else '001'
    intVer = int(f'{major}{minor}{patch}'[0:9])
    return intVer if intVer > 0 else 1


@functools.cache
def depVerAsCudf(depVer):
    op  = depVer[0]
    ver = depVer[1:]

    if depVer[1] == '=':
        op  = depVer[:2]
        ver = depVer[2:]

    return f' {op} {verAsCudf(ver)}'


def compatDep(depSpecs):
    for depSpec in depSpecs:
        if depSpec[0] != "~" and '*' not in depSpec:
            yield depSpec
        elif depSpec[0] in ["~", '=']:
            yield depSpec.replace('*', '0')\
                .replace('~=', '>=')\
                .replace('==', '>=')
            ver    = NO_VER.sub('', depSpec[2:].replace('.*', ''))
            parts  = ver.split('.')
            digits = len(parts)
            major  = (int(parts[0].ljust(1, '0'))) + (1 if digits == 1 else 0)
            minor  = (int(parts[1].ljust(1, '0')) if digits > 1 else 0) + (1 if digits == 2 else 0)
            patch  = (int(parts[2].ljust(1, '0')) if digits > 2 else 0) + (1 if digits >  2 else 0)
            yield f'<{major}.{minor}.{patch}'
        else:
            # TODO: <= a.b.*, != a.*
            yield depSpec.replace('.*', '')


@functools.cache
def depAsCudf(dep):
    # TODO: >==       !?!?
    # TODO: <==       !?!?
    # TODO: <<        !?!?
    # TODO: >>        !?!?
    # TODO: <empty>   !?!?
    # TODO: PKG>      !?!?
    # TODO: dask[bag] !?!?

    parts  = dep.replace(' ', '')\
            .replace('<empty>', '')\
            .strip('=')\
            .strip('>')\
            .strip('<')\
            .strip('=')\
            .replace(',',    '')\
            .replace('>===', '>=')\
            .replace('====', '==')\
            .replace('<===', '<=')\
            .replace('>==',  '>=')\
            .replace('===',  '==')\
            .replace('<==',  '<=')\
            .replace('<<',   '<')\
            .replace('<<',   '<')\
            .replace('>>',   '>')\
            .replace('==',   ' =')\
            .replace('~=',   ' ~=')\
            .replace('!=',   ' !=')\
            .replace('>',    ' >')\
            .replace('<',    ' <')\
            .split(' ')
    name   = nameAsCudf(parts[0])

    if len(parts) == 1:
        return name

    try:
        return ', '.join(name + depVerAsCudf(spec) for spec in compatDep(parts[1:]))
    except:
        print(dep)
        raise


def cudfAll(pkgs):
    for pkg in pkgs:
        yield {
            'package': nameAsCudf(pkg['name']),
            'version': verAsCudf(pkg['version']),
            'python':  pkg['python'],
            'depends': [depAsCudf(req) for req in pkg['requires'] if '//' not in req]
        }


def cudfAsStr(pkg):
    deps = ''
    if len(pkg['depends']):
        deps = '\ndepends: ' + ', '.join(pkg['depends'])

    return f'''\npackage: {pkg['package']}\nversion: {pkg['version']}{deps}\n'''


def writeAll(pkgs):
    cudfFiles = {}
    for pkg in pkgs:
        pyVersion = pkg['python']
        cudfFile = cudfFiles.get(pyVersion)
        if not cudfFile:
            cudfFile = open(f'../cudf/{pyVersion}.cudf', 'w')
            cudfFiles[pyVersion] = cudfFile

        cudfFile.write(cudfAsStr(pkg))

    for cudfFile in cudfFiles.values():
        cudfFile.close()


if __name__ == '__main__':
    files = openAll(sys.argv[1:])
    jsons = loadAll(files)
    pkgs  = pkgAll(jsons)
    cudf  = cudfAll(pkgs)
    writeAll(cudf)
