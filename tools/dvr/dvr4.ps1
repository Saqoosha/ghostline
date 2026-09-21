# Densify the confusing window: 96 more DVR frames, same fisheye camera, registered on top of
# the fixed model that already holds the first 27 DVR frames.
$root   = Join-Path $env:USERPROFILE 'JDL-2026-R6'
$colmap = Join-Path $env:USERPROFILE 'COLMAP\COLMAP-3.12.6-windows-cuda\bin\colmap.exe'
$work   = Join-Path $root 'dvr'; $db = Join-Path $work 'database.db'; $img = Join-Path $root 'images'
$log    = Join-Path $work 'dvr4.log'; $status = Join-Path $work 'dvr4.status'
Set-Content $status "RUNNING $(Get-Date -Format s)"; Set-Content $log "start $(Get-Date -Format s)"
$new = Get-ChildItem (Join-Path $img 'dvr4') -Filter *.jpg | Sort-Object Name | ForEach-Object { "dvr4/$($_.Name)" }
$list = Join-Path $work 'list4.txt'; $pairs = Join-Path $work 'pairs4.txt'   # pairs4.txt comes from the Mac: scan images within 30 m of the interpolated pose, DVR frames within 1 s
[IO.File]::WriteAllLines($list, $new)
Add-Content $log "new=$($new.Count) pairs=$((Get-Content $pairs).Count)"
& $colmap feature_extractor --database_path $db --image_path $img --image_list_path $list `
  --ImageReader.existing_camera_id 2 --ImageReader.camera_mask_path (Join-Path $work 'dvr_mask.png') `
  --SiftExtraction.use_gpu 1 --SiftExtraction.max_num_features 16384 *>> $log
Add-Content $log "feature_extractor rc=$LASTEXITCODE $(Get-Date -Format s)"
& $colmap matches_importer --database_path $db --match_list_path $pairs --match_type pairs --SiftMatching.use_gpu 1 *>> $log
Add-Content $log "matches_importer rc=$LASTEXITCODE $(Get-Date -Format s)"
$out = Join-Path $work 'sparse_fix4'; New-Item -ItemType Directory -Force -Path $out | Out-Null
& $colmap image_registrator --database_path $db --input_path (Join-Path $work 'sparse_fix3') --output_path $out *>> $log
Add-Content $log "image_registrator rc=$LASTEXITCODE $(Get-Date -Format s)"
& $colmap model_converter --input_path $out --output_path $out --output_type TXT *>> $log
Select-String -Path (Join-Path $out 'images.txt') -Pattern ' dvr4?/' | ForEach-Object { $_.Line } | Set-Content (Join-Path $work 'dvr_poses_fix4.txt')
Set-Content $status "DONE $(Get-Date -Format s)"
