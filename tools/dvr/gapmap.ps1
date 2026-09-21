# Continue the reconstruction through the DVR gaps: drop the DVR images the resolver called
# outliers / relabels from sparse_fix4, then run the incremental mapper on top of it with every
# existing frame fixed. image_registrator only fits new images to existing 3D points, so a frame
# that sees only grass never registered; the mapper triangulates new points between DVR frames
# (pairs within 1 s exist in the database) and walks through the gap from its registered edges.
$root   = Join-Path $env:USERPROFILE 'JDL-2026-R6'
$colmap = Join-Path $env:USERPROFILE 'COLMAP\COLMAP-3.12.6-windows-cuda\bin\colmap.exe'
$work   = Join-Path $root 'dvr'; $db = Join-Path $work 'database.db'; $img = Join-Path $root 'images'
$log    = Join-Path $work 'gap.log'; $status = Join-Path $work 'gap.status'
Set-Content $status "RUNNING $(Get-Date -Format s)"; Set-Content $log "start $(Get-Date -Format s)"
$in5 = Join-Path $work 'sparse_fix5'; New-Item -ItemType Directory -Force -Path $in5 | Out-Null
& $colmap image_deleter --input_path (Join-Path $work 'sparse_fix4') --output_path $in5 --image_names_path (Join-Path $work 'delete_images.txt') *>> $log
Add-Content $log "image_deleter rc=$LASTEXITCODE $(Get-Date -Format s)"
$out = Join-Path $work 'sparse_gap'; New-Item -ItemType Directory -Force -Path $out | Out-Null
& $colmap mapper --database_path $db --image_path $img --input_path $in5 --output_path $out `
  --Mapper.fix_existing_frames 1 --Mapper.multiple_models 0 --Mapper.ba_global_frames_ratio 1.5 --Mapper.ba_global_points_ratio 1.5 `
  --Mapper.abs_pose_min_num_inliers 15 --Mapper.init_min_num_inliers 50 --Mapper.num_threads 16 *>> $log
Add-Content $log "mapper rc=$LASTEXITCODE $(Get-Date -Format s)"
& $colmap model_converter --input_path $out --output_path $out --output_type TXT *>> $log
Select-String -Path (Join-Path $out 'images.txt') -Pattern ' dvr[0-9]*/' | ForEach-Object { $_.Line } | Set-Content (Join-Path $work 'dvr_poses_gap.txt')
Set-Content $status "DONE $(Get-Date -Format s)"
